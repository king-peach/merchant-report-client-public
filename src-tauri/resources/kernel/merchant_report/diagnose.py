# -*- coding: utf-8 -*-
"""结构化失败分类 + 诊断包（改版对抗 L2）。

改版最怕两件事: ①故障被误分类成偶发而无人管 ②产生错误数据而不自知。
本模块把失败分成三类, 并在结构性失败时收集证据包(截图+脱敏 DOM 骨架)。

- E_TRANSIENT   网络/慢加载 → 调用方自动重试
- E_AUTH        登录失效   → 走自动登录, 验证码才升级人工
- E_STRUCTURAL  元素找不到/回读不一致/列数异常 → 立即失败, 不臆造, 附诊断包
"""
import json
import os
import re
import time

TRANSIENT_MARKS = ("timeout", "net::", "ERR_CONNECTION", "ERR_NAME", "ERR_INTERNET",
                   "超时", "网络")
AUTH_MARKS = ("登录", "login", "passport", "验证码", "401")

# 结构性指纹: 消息里出现 = 大概率页面结构变了(而非网络抖动)
STRUCTURAL_MARKS = ("未找到", "没有找到", "读不到", "对不上", "not found", "未生效",
                    "列数异常", "逐字节相同", "回读", "不一致", "日期区间设置未生效",
                    "已中止", "AssertionError", "no such element", "querySelector", "未匹配到")


def classify(error_text: str) -> str:
    s = str(error_text)
    low = s.lower()
    # 基础设施类(浏览器被关/配置目录被占用)优先: 这类既非改版也非偶发, 有专门恢复路径
    for rc in ROOT_CAUSES[:2]:
        if any(m.lower() in low for m in rc["marks"]):
            return rc["code"]
    # 结构性优先(改版信号); 登录其次(登录超时≠网络问题); 网络再次
    if any(m.lower() in low for m in STRUCTURAL_MARKS):
        return "E_STRUCTURAL"
    if any(m.lower() in low for m in AUTH_MARKS):
        return "E_AUTH"
    if any(m.lower() in low for m in TRANSIENT_MARKS):
        return "E_TRANSIENT"
    return "E_UNKNOWN"


# ---- 卡点人话解释 + 按分类的动态选项(客户端失败面板用) ----
# choices 顺序即按钮顺序; 第一项为该分类的推荐动作。措辞面向商家, 不用错误码。
EXPLAIN = {
    "E_TRANSIENT": {
        "title": "页面加载慢 / 网络不稳定",
        "hint": "这类问题多数是暂时的，直接重试通常就能恢复。",
        "choices": ["直接重试", "跳过这家店，继续下一家", "结束任务"],
    },
    "E_AUTH": {
        "title": "登录状态失效了",
        "hint": "需要重新登录。若弹出短信验证码，请在自动化浏览器窗口里手动输入，然后回来点「继续」。",
        "choices": ["重新登录", "直接重试", "跳过这家店，继续下一家", "结束任务"],
    },
    "E_STRUCTURAL": {
        "title": "页面结构可能已更新（平台改版）",
        "hint": "这种情况自动修复成功率低。建议先跳过这家店，并点「发送诊断包」——我们会更新适配，你不用改任何东西。",
        "choices": ["发送诊断包", "跳过这家店，继续下一家", "直接重试", "结束任务"],
    },
    "E_UNKNOWN": {
        "title": "出现了未预期的错误",
        "hint": "可先直接重试；若重复出现，请发送诊断包给我们排查。",
        "choices": ["直接重试", "跳过这家店，继续下一家", "发送诊断包", "结束任务"],
    },
}


def explain(code: str) -> dict:
    """错误码 → {title, hint, choices}。未知码按 E_UNKNOWN。"""
    return EXPLAIN.get(str(code)) or EXPLAIN["E_UNKNOWN"]


# ---- 根因定位(确定性优先): 先把已知原因认出来, 再谈 LLM ----
# 设计原则: 能确定的原因绝不交给 LLM 猜; 每条根因自带"该做什么"(action), 而不是让 AI 输出等待参数。
#   auto_recover=True → 编排层可自动恢复(不需要打扰用户), 如重开浏览器
# 顺序即优先级(越靠前越具体)
ROOT_CAUSES = [
    {
        "id": "browser_closed", "code": "E_BROWSER_DEAD", "auto_recover": True,
        "marks": ("target page, context or browser has been closed", "targetclosed",
                  "browser has been closed", "context or browser has been closed",
                  "page has been closed", "browser closed", "target closed"),
        "title": "自动化浏览器被关闭了",
        "hint": "浏览器窗口被手动关掉或被系统回收了。登录状态保存在本机，重开浏览器就能接着跑，不需要重新登录。",
        "action": "relaunch",
        "choices": ["重开浏览器并继续", "跳过这家店，继续下一家", "结束任务"],
    },
    {
        "id": "profile_locked", "code": "E_PROFILE_LOCK", "auto_recover": True,
        "marks": ("singletonlock", "processsingleton", "user data directory is already in use",
                  "profile appears to be in use", "already in use"),
        "title": "浏览器配置目录被占用",
        "hint": "上一次任务残留的浏览器还在运行。关掉残留的浏览器窗口后重试即可（登录状态不受影响）。",
        "action": "relaunch",
        "choices": ["重开浏览器并继续", "跳过这家店，继续下一家", "结束任务"],
    },
    {
        "id": "no_report", "code": "E_NO_REPORT", "auto_recover": False,
        # 只在"平台确实没出这份报表"的措辞上命中; 泛化的"没拿到数据"不归到此(那是未知, 别编原因)
        "marks": ("无可用数据", "无导出文件", "无任何门店", "报表未生成", "平台上还没有",
                  "尚未生成", "报表生成中"),
        "title": "平台上还没有这份报表",
        "hint": "商家后台的日报通常次日才生成。可稍后再跑，或把日期改成更早的一天。",
        "action": "retry",
        "choices": ["直接重试", "发送诊断包", "结束任务"],
    },
    {
        "id": "network", "code": "E_NETWORK", "auto_recover": False,
        "marks": ("err_connection", "err_name_not_resolved", "err_internet", "err_network",
                  "net::", "无法访问", "网络", "dns"),
        "title": "网络连不上商家后台",
        "hint": "检查本机网络或代理设置，恢复后重试。",
        "action": "retry",
        "choices": ["直接重试", "跳过这家店，继续下一家", "结束任务"],
    },
    {
        "id": "login_expired", "code": "E_AUTH", "auto_recover": False,
        "marks": AUTH_MARKS,
        "title": "登录状态失效了",
        "hint": "需要重新登录。若弹出短信验证码，请在自动化浏览器窗口里手动输入，然后回来点「继续」。",
        "action": "relogin",
        "choices": ["重新登录", "直接重试", "跳过这家店，继续下一家", "结束任务"],
    },
    {
        "id": "page_changed", "code": "E_STRUCTURAL", "auto_recover": False,
        "marks": STRUCTURAL_MARKS,
        "title": "页面结构可能已更新（平台改版）",
        "hint": "这种情况自动修复成功率低。建议先跳过这家店，并点「发送诊断包」——我们会更新适配，你不用改任何东西。",
        "action": "diagnose",
        "choices": ["发送诊断包", "跳过这家店，继续下一家", "直接重试", "结束任务"],
    },
    {
        "id": "slow_load", "code": "E_TRANSIENT", "auto_recover": False,
        "marks": TRANSIENT_MARKS,
        "title": "页面加载慢 / 网络不稳定",
        "hint": "这类问题多数是暂时的，直接重试通常就能恢复。",
        "action": "retry",
        "choices": ["直接重试", "跳过这家店，继续下一家", "结束任务"],
    },
]

_UNKNOWN_CAUSE = {
    "id": "unknown", "code": "E_UNKNOWN", "auto_recover": False,
    "title": "出现了未预期的错误",
    "hint": "可先直接重试；若重复出现，请发送诊断包给我们排查。",
    "action": "retry",
    "choices": ["直接重试", "跳过这家店，继续下一家", "发送诊断包", "结束任务"],
}


def root_cause(error_text: str = "", exc_type: str = "") -> dict:
    """失败现场 → 根因(确定性)。识别不出已知原因时才交给 LLM 定位。"""
    hay = f"{error_text} {exc_type}".lower()
    for rc in ROOT_CAUSES:
        if any(m.lower() in hay for m in rc["marks"]):
            return dict(rc)
    return dict(_UNKNOWN_CAUSE)


def _sanitize_dom(page, max_nodes=400) -> list:
    """脱敏 DOM 骨架: 只留 tag/class(脱数字与ID)/可见文本长度, 不留业务数值与凭据。"""
    try:
        return page.evaluate("""(maxNodes) => {
          const out = [];
          const walk = (el, depth) => {
            if (out.length >= maxNodes || depth > 14) return;
            const cls = String(el.className && el.className.baseVal !== undefined
              ? el.className.baseVal : el.className || '');
            const text = (el.children.length === 0 ? (el.textContent || '').trim() : '');
            out.push({
              t: el.tagName.toLowerCase(),
              c: cls.slice(0, 80).replace(/\\d{3,}/g, '#'),
              len: text.length,
              x: text.slice(0, 20).replace(/\\d{3,}/g, '#')
            });
            for (const ch of el.children) walk(ch, depth + 1);
          };
          walk(document.body, 0);
          return out;
        }""", max_nodes)
    except Exception:
        return []


def _page_text(page, limit=6000) -> str:
    """页面文本(主 frame + 子 frame 的 innerText) —— **解析类失败的唯一现场证据**。

    为什么必须带: 脱敏 DOM 骨架在这种 iframe 后台页上只剩 svg/path(len 全 0),
    业务文本全在 frame 里 → 出了"解析对不上"只能靠用户截图猜。此文件只落本地磁盘,
    供开发者更新适配用。"""
    if page is None:
        return ""
    parts = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = []
    for fr in frames:
        try:
            t = fr.evaluate("() => (document.body ? document.body.innerText : '')") or ""
        except Exception:
            t = ""
        if t.strip():
            parts.append(t)
    txt = "\n".join(parts)
    return txt[:limit] + (f"\n…(截断, 共 {len(txt)} 字)" if len(txt) > limit else "")


def _parse_probe() -> dict:
    """美团管家 DOM 直读的解析取证(每个指标的候选总量/渠道明细/是否自洽)。"""
    try:
        from .platforms import gj_mapping
        return dict(gj_mapping.LAST_PROBE or {})
    except Exception:
        return {}


def collect_diagnostic_pack(page, step: str, error_text: str, ddir=None, extra=None) -> str:
    """收集诊断包(截图+脱敏DOM+页面文本+元信息) → 返回文件路径; 失败返回空串。"""
    try:
        out_dir = ddir or os.path.join(os.path.expanduser("~"), ".hermes",
                                       "merchant-report-cache", "diagnostics")
        os.makedirs(out_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(out_dir, f"diag_{step}_{ts}.json")
        shot = ""
        if page is not None:
            try:
                sp = path.replace(".json", ".png")
                page.screenshot(path=sp, timeout=15000)
                shot = sp
            except Exception:
                pass
        info = {
            "time": ts,
            "step": step,
            "error": str(error_text)[:500],
            "classification": classify(error_text),
            "url": (page.url[:200] if page is not None else ""),
            "frames": ([f.url[:160] for f in page.frames] if page is not None else []),
            "pack_version": _pack_versions(),
            "screenshot": shot,
            "dom_skeleton": _sanitize_dom(page) if page is not None else [],
            "page_text": _page_text(page),
            "parse_probe": _parse_probe(),
        }
        if extra:
            info.update(extra)
        json.dump(info, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return path
    except Exception:
        return ""


def _pack_versions():
    from . import packs
    vers = {}
    for pf in ("meituan", "taobao", "jd", "meituan_gj"):
        p = os.path.join(packs.LOCAL_DIR, f"{pf}.json")
        if os.path.exists(p):
            try:
                vers[pf] = json.load(open(p, encoding="utf-8")).get("pack_version")
            except Exception:
                pass
    return vers
