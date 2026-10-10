# -*- coding: utf-8 -*-
"""卡点交互(P0): 失败 → 用户看得懂的卡点详情 + 按分类的动态选项 → 返回动作。

与 selfheal 的分工:
- selfheal  : 现场上下文 + LLM 诊断 + 白名单补丁(改版对抗 L2)
- failure_ui: 把「卡在哪 / 为什么 / 我能做什么」讲给用户, 并收集选择(批量韧性)

红线: 用户选择只映射到既有确定性分支(retry/skip/relogin/abort), 不生成代码。
批量韧性: 未收到选择(壳不可用/超时)时按「跳过继续」处理, 不中断整批任务。
"""
import base64

from .diagnose import classify, collect_diagnostic_pack, explain, root_cause

# 壳不可用时的保守超时(秒) — 由壳自身控制真正超时, 这里只是协议值
DEFAULT_TIMEOUT_S = 600


def _shot_data_url(page, quality=55, max_w=1000):
    """失败现场截图 → data URL(缩略 JPEG, 供客户端面板内嵌显示)。失败返回 ''。"""
    if page is None:
        return ""
    try:
        raw = page.screenshot(type="jpeg", quality=quality, timeout=8000)
        if not raw:
            return ""
        return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    except Exception:
        return ""


def ask_failure(sink, human, *, platform="", store="", date="", step="",
                error_text="", err_code=None, exc_type="", page=None, allow_skip=True,
                allow_relogin=False, diagnose_dir=None, extra=None, attempt=0,
                max_retry=2, cause=None):
    """弹卡点面板并等用户选择。

    返回 {"action": "retry"|"skip"|"relogin"|"relaunch"|"diagnose"|"abort", "choice": str}
      - retry   : 用户要求重试(调用方负责重试并处理 attempt 上限)
      - skip    : 跳过当前对象(门店), 继续下一个
      - relogin : 用户要求重新登录(仅 allow_relogin 时出现)
      - relaunch: 重开自动化浏览器(浏览器被关闭/配置目录被占用时)
      - diagnose: 已生成诊断包
      - abort   : 结束任务
    根因先用确定性规则识别(root_cause), 识别不出才退回错误码分类。
    """
    c = cause or root_cause(error_text, exc_type)
    if c.get("id") == "unknown":
        # 确定性规则没认出来 → 退回错误码分类(仍给用户可读解释)
        code = err_code or classify(error_text)
        c = dict(c, code=code, **explain(code))
    code = err_code or c.get("code") or "E_UNKNOWN"
    title, hint = c.get("title", ""), c.get("hint", "")
    choices = list(c.get("choices") or explain(code)["choices"])
    if not allow_skip:
        choices = [c2 for c2 in choices if "跳过" not in c2]
    if not allow_relogin:
        choices = [c2 for c2 in choices if "重新登录" not in c2]
    elif not any("重新登录" in c2 for c2 in choices):
        # 登录过期是最常见的失败原因之一, 允许重登的场景要主动给出该选项(插在推荐项之后)
        choices.insert(1 if len(choices) > 1 else 0, "重新登录")
    if attempt >= max_retry:
        choices = [c2 for c2 in choices if "重试" not in c2] or ["结束任务"]

    shot = _shot_data_url(page)
    ctx = {
        "platform": platform, "store": store, "date": date, "step": step,
        "error_code": code, "root_cause": c.get("id", "unknown"),
        "title": title, "hint": hint, "suggested_action": c.get("action", ""),
        "error": str(error_text)[:300], "shot": shot, "attempt": attempt + 1,
    }
    if extra:
        ctx.update({k: v for k, v in extra.items() if v})

    parts = [title]
    if store:
        parts.append(f"门店：{store}")
    if date:
        parts.append(f"日期：{date}")
    if step:
        parts.append(f"步骤：{step}")
    sink.need_human("confirm", " ｜ ".join(parts), platform=platform or None,
                    choices=choices, context=ctx, timeout_s=DEFAULT_TIMEOUT_S)
    resp = human.wait({"type": "need_human", "action": "confirm"}) or {}
    choice = str(resp.get("choice") or "").strip()

    if not choice:
        # 壳不可用(CLI)/超时 → 保守: 有可跳过的对象就继续, 否则结束
        act = "skip" if allow_skip else "abort"
        sink.log(f"⚠️ 未收到用户选择(超时/无人值守)，按「{'跳过继续' if act == 'skip' else '结束任务'}」处理",
                 level="warn")
        return {"action": act, "choice": "(无响应)", "root_cause": c.get("id")}

    if any(k in choice for k in ("结束", "放弃", "取消")):
        return {"action": "abort", "choice": choice, "root_cause": c.get("id")}
    if "诊断" in choice:
        p = collect_diagnostic_pack(page, step or "unknown", error_text, diagnose_dir)
        if p:
            sink.log(f"📦 诊断包已生成（发给开发者即可更新适配）: {p}")
        else:
            sink.log("⚠️ 诊断包生成失败", level="warn")
        return {"action": "diagnose", "choice": choice, "pack": p, "root_cause": c.get("id")}
    if "跳过" in choice or "下一家" in choice:
        return {"action": "skip", "choice": choice, "root_cause": c.get("id")}
    if "重新登录" in choice:
        return {"action": "relogin", "choice": choice, "root_cause": c.get("id")}
    if "浏览器" in choice and ("重开" in choice or "重启" in choice):
        return {"action": "relaunch", "choice": choice, "root_cause": c.get("id")}
    return {"action": "retry", "choice": choice, "root_cause": c.get("id"),
            "free_text": str(resp.get("free_text") or "").strip()}
