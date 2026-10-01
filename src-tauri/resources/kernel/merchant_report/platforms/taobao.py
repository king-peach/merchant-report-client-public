# -*- coding: utf-8 -*-
"""淘宝闪购适配器: 登录检测 / 报表下载(全部+爆品团) / 流量6态抓取(前10%均值) / 推广页。
移植自已验证的 run_report.py + capture_top10.py(2026-09-16 e2e)。
浏览器: Playwright 驱动系统 Edge/Chrome(回退链), 不打包内核。"""
import datetime
import json
import os
import re
import shutil
import time
from pathlib import Path

from ..events import EventSink
from ..fill import canonical_report_name, platform_range_in_name, report_kind

DOWNLOAD_CENTER = "https://melody.shop.ele.me/app/shop/{shop_id}/downloadCenter#app.shop.downloadCenter"
STATS_HOME = "https://melody.shop.ele.me/"


# ---------- 浏览器启动 ----------
# 方案演进: Playwright launch(滑块被风控拒, RVPrh) → CDP 附着(Chrome 152 禁 Browser.setDownloadBehavior)
# 最终: launch_persistent_context + ignore_default_args 剔除自动化痕迹参数
# (--enable-automation 等是滑块被拒的元凶; 剔除后 navigator.webdriver=false, 滑块可过)

# 单次启动超时(ms): Playwright 默认 180s, 三个候选串行会等 9 分钟才报错 → 压到 90s
LAUNCH_TIMEOUT_MS = 90000


def _cleanup_stale_browser(profile_dir):
    """结束仍占用本 profile 的残留浏览器(上一轮任务未完全退出)。
    否则新启动的浏览器会把参数转发给旧实例, Playwright 拿不到控制权。
    只结束命令行里带本 profile 路径的进程, 不动用户自己开的浏览器窗口。"""
    prof = str(profile_dir)
    try:
        if os.name == "nt":
            esc = prof.replace("'", "''")
            ps = ("Get-CimInstance Win32_Process "
                  f"| Where-Object {{ $_.CommandLine -like '*{esc}*' }} "
                  "| ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=25)
        else:
            out = subprocess.run(["pgrep", "-f", prof], capture_output=True, text=True,
                                 timeout=5).stdout.split()
            for pid in out:
                try:
                    os.kill(int(pid), 15)
                except Exception:
                    pass
            if out:
                time.sleep(3)   # 等浏览器完全退出释放 profile 锁
    except Exception:
        pass


def _launch_persistent(pw, profile_dir, args, exclude, channel=None, timeout_ms=LAUNCH_TIMEOUT_MS):
    kw = dict(headless=False, args=args, ignore_default_args=exclude,
              viewport=None, accept_downloads=True, timeout=timeout_ms)
    if channel:
        kw["channel"] = channel
    return pw.chromium.launch_persistent_context(str(profile_dir), **kw)


def verify_browser(exe="", timeout_ms=45000):
    """下载后立刻试启动一次(headless), 尽早发现"文件在但跑不起来"(杀软拦截/下载损坏)。
    返回 (ok, 说明)。只在刚下载完时调用, 避免每次任务都多花几秒。"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(headless=True, timeout=timeout_ms)
                b.close()
                return True, ""
            except Exception as e:
                return False, str(e)[:220]
    except Exception as e:
        return False, str(e)[:150]


# 可继续尝试下一个候选的错误关键字: 不只是"找不到浏览器", 启动超时同样要换候选
FALLBACK_MARKS = ("executable doesn't exist", "failed to launch", "channel", "not found",
                  "cannot find", "没有", "timeout", "timed out", "target closed")


def launch_browser(pw, profile_dir, prefer=None):
    """Playwright 启动 Chromium 系浏览器(持久 profile + 剔除自动化痕迹参数)。
    候选顺序 = **本机记忆**优先(见 browser_pref): 
      - 上次成功的候选排最前(用户 VPS 上自带 Chromium 起不来、系统 Chrome 正常 → 直接记住 Chrome)
      - 自带 Chromium 若被判过不可用则排到最后, 避免每次白等 90 秒超时
    也支持环境变量 MRC_BROWSER_CHANNEL=chrome|msedge 强制指定。
    macOS 上实测自带 Chromium 可用(滑块能过), 默认顺序不变。"""
    _cleanup_stale_browser(profile_dir)
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
    ]
    exclude = [
        "--enable-automation",
        "--enable-blink-features=IdleDetection",
        "--no-sandbox",
    ]
    from ..browser_pref import order as _order, remember_ok
    forced = prefer or os.environ.get("MRC_BROWSER_CHANNEL") or None
    order = _order([None, "msedge", "chrome"], forced=forced)
    errs = []
    for ch in order:
        try:
            ctx = _launch_persistent(pw, profile_dir, args, exclude, ch)
            if not forced:
                remember_ok(ch)      # 记住"这台机器上能用哪个", 下次直接用
            return ctx
        except Exception as e:
            msg = str(e)
            # 保留足够长的原始信息(Call log 里通常有浏览器自身的报错, 是定位关键)
            errs.append(f"{ch or 'chromium(自带)'}: {msg[-400:]}")
            low = msg.lower()
            if low.find("timeout") >= 0 or low.find("timed out") >= 0:
                from ..browser_pref import remember_broken
                if ch is None:
                    remember_broken()       # 自带 Chromium 启动超时 → 下次排到最后
            # 只有"浏览器不存在/启动超时"才换下一个候选; 其他错误(profile 锁等)原样抛
            if not any(k in low for k in FALLBACK_MARKS):
                raise
    raise RuntimeError("无法启动浏览器。已尝试: " + " ｜ ".join(errs)
                       + " ｜ 怎么办: ①装/更新 Microsoft Edge 或 Chrome; "
                         "②若装了安全软件(360/腾讯电脑管家/Windows Defender), 把本程序与 "
                         "AppData\\Roaming\\MerchantReportClient 加入白名单; "
                         "③关掉仍开着的自动化浏览器窗口后重试; "
                         "④云服务器/远程桌面环境建议装 Google Chrome(自带 Chromium 可能起不来)。")


class TaobaoAdapter:
    platform = "taobao"

    def __init__(self, ctx, sink: EventSink, human_bridge, store_id="", keyword=""):
        self.ctx = ctx
        self.sink = sink
        self.human = human_bridge
        self.page = None
        self.store_id = store_id or ""
        self.keyword = keyword or ""
        self.shop_id = ""     # 登录后从主页 DOM 提取(链接 /app/shop/<id>/)
        # AI 自愈补丁覆盖项(selfheal.apply_patch_hint 写入)
        self.nav_override = None
        self.wait_override_ms = None
        # 身份验证策略(L1): 默认不程序化拖滑块——合成轨迹会被判失败并推高风险评分,
        # 把人工通过的路也堵死。True 时启用旧的合成轨迹拖动(见 autologin._human_drag)。
        self.auto_slider = False

    # ---------- 登录 ----------
    # 登录成功标志: 登录页/未登录态也有"商家版"字样(品牌区), 不能作为判据。
    # 用只对已登录用户渲染的元素: 左侧菜单项(订单管理/数据中心/顾客管理/单店账号)。
    _LOGIN_MARKERS = ["订单管理", "数据中心", "顾客管理", "单店账号"]
    # 学习/引导/公告类弹窗的关闭按钮文案
    _POPUP_CLOSE_HINTS = ["我知道了", "知道了", "不再提示", "跳过", "下次再说", "开始使用", "立即体验", "去学习", "完成", "关闭"]

    def ensure_login(self, home=STATS_HOME, timeout_s=300):
        """已登录复用; 未登录打开首页+need_human(login), 轮询等待; 登录后清弹窗"""
        if self._logged_in():
            self.sink.log("✅ 复用已有登录会话")
            self._dismiss_popups()
            self._discover_shop_id()
            return True
        self.page = self.ctx.new_page() if not self.ctx.pages else self.ctx.pages[0]
        self.page.goto(home, wait_until="commit")
        time.sleep(5)
        if self._logged_in():
            self._dismiss_popups()
            self._discover_shop_id()
            return True
        self.sink.need_human("login", "请在弹出的浏览器中完成登录/滑块验证", platform="taobao",
                             timeout_s=timeout_s)
        end = time.time() + timeout_s
        while time.time() < end:
            time.sleep(4)
            if self._logged_in():
                self.sink.log("✅ 登录成功")
                time.sleep(2)
                self._dismiss_popups()
                self._discover_shop_id()
                return True
        self.sink.step_fail("login", "登录", "超时未检测到登录", "E_LOGIN_TIMEOUT")
        return False

    def _discover_shop_id(self):
        """从已打开页面的链接里提取 shop_id (/app/shop/<数字>/)"""
        import re as _re
        for p in self.ctx.pages:
            if "melody.shop.ele.me" not in p.url:
                continue
            try:
                hits = p.evaluate("""() => {
                  const out = [];
                  for (const a of document.querySelectorAll('a[href*="/app/shop/"]')) {
                    out.push(a.href);
                  }
                  // 页面 URL 本身也可能带
                  if (location.href.includes('/app/shop/')) out.push(location.href);
                  return out;
                }""")
                for u in hits or []:
                    m = _re.search(r"/app/shop/(\d+)", u)
                    if m:
                        self.shop_id = m.group(1)
                        self.sink.log(f"🆔 发现店铺ID: {self.shop_id}")
                        return self.shop_id
            except Exception:
                pass
        return ""

    def _logged_in(self):
        for p in self.ctx.pages:
            if "melody.shop.ele.me" not in p.url:
                continue
            try:
                txt = p.evaluate("() => document.body ? document.body.innerText.slice(0, 2000) : ''")
                hits = sum(1 for m in self._LOGIN_MARKERS if m in txt)
                if hits >= 2:
                    self.page = p
                    return True
            except Exception:
                pass
        return False

    def _dismiss_popups(self):
        """关掉商家后台的学习/引导/公告类弹窗(循环多层)"""
        closed = 0
        for _ in range(6):
            try:
                r = self.page.evaluate("""(hints) => {
                  let closed = 0;
                  const containers = document.querySelectorAll('[class*="modal"], [class*="Modal"], [class*="dialog"], [class*="Dialog"], [class*="popover"], [class*="guide"], [class*="Guide"], [class*="drawer"], [class*="mask"], [class*="Mask"], [class*="float"], [class*="Float"]');
                  for (const box of containers) {
                    const r = box.getBoundingClientRect();
                    if (!(r.width > 100 && r.height > 60)) continue;
                    for (const btn of box.querySelectorAll('button, a, [role=button], [class*="btn"], [class*="close"], [class*="Close"], [class*="icon"]')) {
                      const t = (btn.innerText || '').trim();
                      const cls = (typeof btn.className === 'string' ? btn.className : '');
                      const rr = btn.getBoundingClientRect();
                      if (rr.width <= 0) continue;
                      if (hints.some(h => t.includes(h)) || /close|Close/i.test(cls)) {
                        btn.click(); closed++; break;
                      }
                    }
                  }
                  return closed;
                }""", self._POPUP_CLOSE_HINTS)
            except Exception:
                break
            if not r:
                break
            closed += r
            time.sleep(1)
        if closed:
            self.sink.log(f"🧹 已关闭 {closed} 个引导/公告弹窗")
        return closed


    # ---------- 报表下载 ----------
    # 下载页那两个日期输入框(可见 + 值形如 2026-09-28)
    _DATES_JS = """() => {
      return [...document.querySelectorAll('input')].filter(i => {
        const r = i.getBoundingClientRect();
        return r.width > 40 && r.height > 0 && /^\\d{4}-\\d{2}-\\d{2}$/.test((i.value || '').trim());
      }).map(e => {
        const r = e.getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2, v: e.value.trim()};
      });
    }"""

    def _date_inputs(self, f):
        try:
            return f.evaluate(self._DATES_JS) or []
        except Exception:
            return []

    def _js_set_dates(self, f, df, dt):
        return f.evaluate("""(args) => {
          const [df, dt] = args;
          const inputs = [...document.querySelectorAll('input')].filter(i => {
            const r = i.getBoundingClientRect();
            return r.width > 40 && /\\d{4}-\\d{2}-\\d{2}/.test(i.value || '');
          });
          if (inputs.length < 2) return 0;
          const set = (el, v) => {
            const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            s.call(el, v);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
          };
          set(inputs[0], df); set(inputs[1], dt);
          return 2;
        }""", [df, dt])

    def _type_dates(self, f, df, dt):
        """点进日期框 + 键盘输入(React 日期控件认键盘事件, 比直接写 value 稳)。"""
        for i, d in enumerate((df, dt)):
            try:
                ins = self._date_inputs(f)
                if len(ins) < 2:
                    return
                pt = ins[i]
                self.page.mouse.click(pt["x"], pt["y"])
                time.sleep(0.4)
                for key in ("Meta+A", "Control+A"):     # mac/win 各清一次(不相干的键是无害空操作)
                    try:
                        self.page.keyboard.press(key)
                    except Exception:
                        pass
                self.page.keyboard.type(d, delay=45)
                time.sleep(0.3)
                self.page.keyboard.press("Enter")
                time.sleep(0.8)
            except Exception as e:
                self.sink.log(f"⚠️ 键盘输入日期失败({d}): {str(e)[:60]}", level="warn")

    def _ensure_day_mode(self, f):
        """下载页有「汇总 | 按天 | 按月」三粒度 —— **按月**时日期控件是月选择器, 写日期必然不生效。

        2026-10-01 真机: 诊断截图里日期显示「2026-08 至 2026-08」= 当时停在按月模式,
        于是内核写 2026-09-29 怎么都写不进去(读回 09-30 → 键盘重试 → 空) → 任务中止。
        """
        try:
            r = f.evaluate("""() => {
              const wraps = [...document.querySelectorAll('[class*=dateFilter--filterWrapper]')];
              const wrap = wraps.find(w => /按天/.test(w.innerText||'') && /按月/.test(w.innerText||''));
              if (!wrap) return 'noframe';
              const items = [...wrap.querySelectorAll('*')].filter(e =>
                /^(按天|按月|汇总)$/.test((e.innerText||'').trim()) && e.children.length <= 1);
              const day = items.find(e => (e.innerText||'').trim() === '按天');
              if (!day) return 'noday';
              if (/activated/.test((day.className||'').toString())) return 'already';
              day.click();
              return 'clicked';
            }""")
            if r == "clicked":
                self.sink.log("🔁 下载页粒度切到「按天」(原来是按月, 日期控件是月选择器)")
                time.sleep(1.5)
            return r
        except Exception as e:
            self.sink.log(f"⚠️ 切「按天」失败: {str(e)[:60]}", level="warn")
            return "err"

    def _set_range_via_calendar(self, f, d1, d2):
        """antd 区间面板: 点开输入框 → (必要时往前翻月) → 点 title=<日期> 的格子。

        单日区间 = 同一格点两次(antd 语义: 第一次设起点, 第二次补齐终点)。
        """
        try:
            loc = f.locator(".ant-picker-range input, .ant-picker input")
            if not loc.count():
                return False
            loc.first.click(timeout=5000)
            time.sleep(1.2)
            for _ in range(24):
                if f.locator(f'td[title="{d1}"]').count():
                    break
                prev = f.locator(".ant-picker-header-prev-btn")
                if not prev.count():
                    break
                prev.first.click()
                time.sleep(0.5)
            cell = f.locator(f'td[title="{d1}"]')
            if not cell.count():
                self.sink.log(f"⚠️ 日历里找不到 {d1} 的格子", level="warn")
                return False
            cell.first.click(timeout=5000)
            time.sleep(0.7)
            if d2 and d2 != d1 and f.locator(f'td[title="{d2}"]').count():
                f.locator(f'td[title="{d2}"]').first.click(timeout=5000)
            elif cell.count():
                cell.first.click(timeout=5000)      # 同一格再点一次 = 补齐区间
            time.sleep(1.2)
            return True
        except Exception as e:
            self.sink.log(f"⚠️ 日历选日期失败: {str(e)[:70]}", level="warn")
            return False

    def _set_date_range(self, f, date_from, date_to):
        """设下载中心日期区间 → **读回校验**; 直接写不生效就键盘输入; 仍不生效就**中止**。

        为什么必须校验(2026-09-30 真机): 日期控件是 React 的, JS 直接写 value + input/change
        有时它**不认**(内部 state 没变) → 表单按旧区间提交 → 平台生成的是**别的日期**的报表
        (请求 2026-09-28, 下回来的是 09-29) → 按"名字区间 + 内容日期"筛选时两份都被排除,
        报「缺全部/爆品团报表（已有: 无）」, 用户完全看不出是日期没设上。
        美团那边早就有这道校验(不生效就中止); 淘宝这次补齐 —— 宁可中止, 也不用错区间的数据填表。
        """
        ins = self._date_inputs(f)
        if len(ins) < 2:
            self.sink.log(f"⚠️ 日期区间控件没找到(共 {len(ins)} 个), 沿用页面默认区间 —— "
                          f"下回来的报表会按内容日期校验, 不一致不会填表", level="warn")
            return 0
        try:
            self._js_set_dates(f, date_from, date_to)
        except Exception as e:
            self.sink.log(f"⚠️ 写入日期异常: {str(e)[:60]}", level="warn")
        time.sleep(1)
        got = [x["v"] for x in self._date_inputs(f)][:2]
        if got[:2] != [date_from, date_to]:
            self.sink.log(f"⚠️ 直接写入日期未生效(页面仍是 {got[:2]}) → 先确保「按天」粒度, 再用日历格子选",
                          level="warn")
            self._ensure_day_mode(f)                    # 按月模式 → 切回按天(月选择器写不了具体日期)
            self._set_range_via_calendar(f, date_from, date_to)
            got = [x["v"] for x in self._date_inputs(f)][:2]
        if got[:2] != [date_from, date_to]:             # 最后一层兜底: 键盘输入
            self.sink.log(f"⚠️ 日历选日期仍未生效(页面 {got[:2]}), 改键盘输入重试", level="warn")
            self._type_dates(f, date_from, date_to)
            got = [x["v"] for x in self._date_inputs(f)][:2]
        if got[:2] != [date_from, date_to]:
            raise RuntimeError(
                f"下载页日期区间设置未生效: 期望 {date_from}~{date_to}, 页面实际 {got[:2]}"
                f"（已中止，避免下载到别的区间的报表、把别的日期数据填进模板）")
        self.sink.log(f"📅 下载区间已确认 = {got[0]} ~ {got[1]}")
        return 2

    def _dc_frames(self):
        """按"像不像下载中心"给所有 iframe 打分排序, 返回 frame 列表(最像的在前)。
        不同账号视角/改版下嵌套层级与文案不同(品牌版 vs 单店版: 数据中心→数据下载),
        所以不假定唯一 frame, 由调用方逐个尝试。"""
        scored = []
        for pg in self.ctx.pages:
            for fr in pg.frames:
                try:
                    url = fr.url or ""
                    txt = fr.evaluate(
                        "() => document.body ? document.body.innerText.slice(0, 800) : ''") or ""
                except Exception:
                    continue
                score = 0
                if "download-center" in url:
                    score += 10
                for lab in ("下载数据", "报表下载", "下载管理", "数据下载", "下载中心"):
                    if lab in txt:
                        score += 2
                if score:
                    scored.append((score, fr))
        scored.sort(key=lambda x: -x[0])
        return [fr for _, fr in scored]

    def _dc_frame(self, quiet=False):
        frames = self._dc_frames()
        return frames[0] if frames else None

    def _frame_report(self, limit=8):
        """失败现场清单: 每个 frame 的 url + 文本开头。用于远程定位"页面上到底有什么"。"""
        out = []
        for pg in self.ctx.pages:
            for fr in pg.frames:
                try:
                    t = fr.evaluate("() => document.body ? document.body.innerText : ''") or ""
                except Exception:
                    t = "(读取失败)"
                t = " ".join(str(t).split())[:110]
                out.append(f"[{len(out) + 1}] {fr.url[:80]} :: {t}")
                if len(out) >= limit:
                    return " ｜ ".join(out)
        return " ｜ ".join(out) if out else "(没有任何 frame)"

    BRAND_HOME = "https://melody.shop.ele.me/app/unit/dashboard"

    def reset_shop_context(self, page=None):
        """把右上角门店上下文切回**账号/品牌级**。

        为什么必须: 2026-10-01 真机 —— 逐店抓取/人工浏览之后上下文会停在某个单店,
        再跑「全部门店」任务时下载页就在单店上下文里打开(日志: 发现店铺ID 444444444 →
        直链打开下载中心(shop 444444444)), 日期控件随之失效 → 下载步骤中止。

        实现: **直接导航到品牌级 URL**。/app/unit/dashboard 落地后右上角=账号名(如 demo_account)、
        _discover_shop_id() 返回空 —— 实测比点门店下拉可靠得多(那个弹层在脚本流程里时开时不开)。
        """
        page = page or self.page
        try:
            page.goto(self.BRAND_HOME, wait_until="domcontentloaded")
            time.sleep(4)
            self.shop_id = ""
            self.sink.log(f"🔄 门店上下文已复位到账号级({self._shop_name(page) or '?'})")
            return True
        except Exception as e:
            self.sink.log(f"⚠️ 复位门店上下文失败: {str(e)[:60]}", level="warn")
            return False

    def download_reports(self, ddir, timeout_s=300, date_from=None, date_to=None):
        """提交 全部+爆品团 两份下载任务并等待落盘。
        scope=store 时用 self.store_id 拼 URL; 否则走菜单导航(账号默认门店上下文)。
        直链与菜单导航**互为回退**(账号视角不同, 单条路可能不成立)。"""
        # 全部门店任务: 先复位到账号级 —— 上下文被留在某个单店时(逐店抓取/上次浏览),
        # 下载页会以单店上下文打开且日期控件不生效 → 下载步骤中止(2026-10-01 真机踩过)。
        if not self.store_id and self.shop_id:
            self.reset_shop_context()
        sid = self.store_id or self.shop_id
        nav_ok = False
        if sid:
            self.sink.log(f"📂 直链打开下载中心 (shop {sid})")
            try:
                self._open_download_center(sid)
                nav_ok = True
            except Exception as e:
                self.sink.log(f"⚠️ 直链失败({str(e)[:70]}), 改用菜单导航(数据中心→数据下载)", level="warn")
        if not nav_ok:
            self.sink.log("📂 菜单导航: 数据中心 → 数据下载…")
            self._open_download_center_via_menu()
        # 下载落地: 用 Playwright 原生 download 事件接管(不依赖 Browser.setDownloadBehavior
        # —— 新版 Chrome(152+) 会拒绝/忽略我们直接发的这个 CDP 调用, 症状是文件下到别处、
        #   我们永远等不到文件 → "下载报表没有拿到数据")。
        dl_dir = Path(ddir)
        self._dl_events = 0   # 收到几次下载事件(诊断: 0=浏览器侧根本没触发下载)
        try:
            self.ctx.on("download", self._on_download(dl_dir))
            self.sink.log("💾 已挂载下载接管(Playwright download 事件)")
        except Exception as e:
            self.sink.log(f"⚠️ 下载接管挂载失败: {str(e)[:80]}", level="warn")
        try:
            cdp = self.ctx.new_cdp_session(self.page)
            cdp.send("Browser.setDownloadBehavior",
                     {"behavior": "allow", "downloadPath": ddir, "eventsEnabled": True})
        except Exception as e:
            # 不强求: 上面的事件接管已能落盘; 这里失败只提示, 不阻塞
            self.sink.log(f"ℹ️ CDP 下载行为设置被浏览器拒绝(已由事件接管兜底): {str(e)[:80]}")
        # 记住本次请求区间: 下载事件 _save_download 用它给文件改规范名
        # (平台原名末段是**下载时间戳**不是报表日期, 分不清是哪天的报表)
        self._dl_range = (date_from or date_to or "", date_to or date_from or "")
        before = set(os.listdir(ddir))
        f = self._open_form_frame()
        if date_from and date_to:
            self.sink.log(f"📅 下载区间: {date_from} ~ {date_to}")
            self._set_date_range(f, date_from, date_to)
        submitted = []
        for otype in ("全部", "爆品团"):
            if self._submit_task(f, otype):
                submitted.append(otype)
            time.sleep(3)
        if not submitted:
            raise RuntimeError("下载表单里没有任何可选的订单类型(说明没真正进到报表下载页)"
                               "｜现场: " + self._frame_report())
        self._goto_manager(f)
        got = self._wait_files(f, ddir, before, timeout_s, need=len(submitted))
        return got

    def _open_form_frame(self):
        """在候选 frame 里逐个找「报表下载」表单并点进去; 全失败时带上现场清单报错。
        候选取自打分排序的 iframe, 并以**主页面**兜底(入口可能不在 iframe 里)。"""
        frames = self._dc_frames()
        try:
            main = self.page.main_frame
            if main is not None and main not in frames:
                frames = frames + [main]
        except Exception:
            pass
        errs = []
        for f in frames[:6]:
            try:
                self._goto_form(f)
                return f
            except Exception as e:
                errs.append(str(e)[:60])
        raise RuntimeError("找不到「报表下载」入口(已试 %d 个候选: %s)｜现场: %s"
                           % (len(frames[:6]), "; ".join(errs[:3]) or "无候选", self._frame_report()))

    def _open_download_center(self, shop_id, rounds=3):
        for rnd in range(rounds):
            self.page.goto(DOWNLOAD_CENTER.format(shop_id=shop_id), wait_until="commit")
            for _ in range(15):
                time.sleep(2)
                f = self._dc_frame(quiet=True)
                if f:
                    try:
                        if "数据下载" in f.evaluate("() => document.body ? document.body.innerText : ''") or \
                           "下载管理" in f.evaluate("() => document.body ? document.body.innerText : ''"):
                            return
                    except Exception:
                        pass
            self.sink.log(f"[重试{rnd+1}/3] 下载中心未就绪")
        raise RuntimeError("下载中心 iframe 未出现")

    def _open_download_center_via_menu(self, rounds=3):
        """无 shop_id / 直链失败时: 左侧菜单 数据中心 → 数据下载(单店版路径)。
        品牌版可能是 数据中心 → 报表下载; 菜单收起态需 hover 展开。失败时打印现场清单。"""
        for rnd in range(rounds):
            self.page.goto(STATS_HOME, wait_until="commit")
            time.sleep(4)
            self._dismiss_popups()
            opened = False
            for parent in ("数据中心", "数据"):
                try:
                    nav = self.page.get_by_text(parent, exact=True).first
                    if not (nav.count() and nav.is_visible()):
                        continue
                    nav.hover()                 # 收起态子菜单靠 hover 才出现
                    time.sleep(0.8)
                    nav.click()
                    time.sleep(1.5)
                    opened = True
                    break
                except Exception:
                    continue
            if opened:
                for lab in ("数据下载", "报表下载", "下载中心", "下载管理"):
                    try:
                        sub = self.page.get_by_text(lab, exact=True).first
                        if sub.count() and sub.is_visible():
                            self.sink.log(f"🔎 菜单入口: {parent} → {lab}")
                            sub.click()
                            break
                    except Exception:
                        continue
            if not opened:
                # 备选: 经营分析 frame 内的「数据下载」子菜单
                try:
                    bf = [fr for fr in self.page.frames if "business-analysis" in fr.url][0]
                    bf.get_by_text("数据下载", exact=True).first.click()
                except Exception:
                    self.sink.log(f"[导航] 菜单未见下载入口, 重试{rnd + 1}/{rounds}")
                    continue
            for _ in range(15):
                time.sleep(2)
                if self._dc_frame(quiet=True):
                    return
            self.sink.log(f"[重试{rnd + 1}/{rounds}] 下载中心未就绪")
        raise RuntimeError("下载中心 iframe 未出现(菜单导航)｜现场: " + self._frame_report())

    def _goto_form(self, f):
        """进入「报表下载」表单页。已在表单页(有"下载数据")直接返回; 否则依次尝试多种入口文案
        (品牌版叫「报表下载」、单店版在 数据中心→数据下载 下), 并兼顾"文字不可点、要点其父级"的情况。"""
        self._dismiss_popups()
        try:
            if "下载数据" in (f.evaluate("() => document.body.innerText") or ""):
                return
        except Exception:
            pass
        for lab in ("报表下载", "数据下载", "下载数据", "下载中心"):
            try:
                el = f.get_by_text(lab, exact=True)
                n = min(el.count(), 4)
            except Exception:
                continue
            for i in range(n):
                try:
                    if not el.nth(i).is_visible():
                        continue
                    el.nth(i).click(timeout=5000)
                    time.sleep(2.5)
                    return
                except Exception:
                    # 文字可能挂在菜单容器上不可点 → 点它最近的 li/a/menuitem
                    try:
                        el.nth(i).evaluate(
                            "e => { const p = e.closest('li,a,[role=\"menuitem\"]'); (p||e).click(); }")
                        time.sleep(2.5)
                        return
                    except Exception:
                        continue
        # JS 兜底: 直接点页面上文案匹配的第一个可见元素
        try:
            hit = f.evaluate("""() => {
              const labs = ['报表下载','数据下载','下载数据','下载中心'];
              const els = [...document.querySelectorAll('a,li,div,span,button')];
              for (const lab of labs) {
                const el = els.find(e => (e.textContent||'').trim() === lab
                  && e.getBoundingClientRect().width > 0);
                if (el) { (el.closest('a,li,[role="menuitem"]') || el).click(); return lab; }
              }
              return '';
            }""")
            if hit:
                self.sink.log(f"🔎 已点击入口「{hit}」(JS 兜底)")
                time.sleep(2.5)
                return
        except Exception:
            pass
        raise RuntimeError("找不到「报表下载」入口")

    def _on_download(self, dl_dir):
        """download 事件处理器(带计数): 计数用于超时时判断「浏览器没触发」还是「文件被丢弃」。"""
        def _h(d):
            self._dl_events = getattr(self, "_dl_events", 0) + 1
            self._save_download(d, dl_dir)
        return _h

    def _recover_stray_downloads(self, ddir, max_age_min=40):
        """兜底: 下载事件没能落盘时, 去浏览器默认下载目录把本次文件**复制**回来(不移动原文件)。

        为什么需要: Browser.setDownloadBehavior 在部分环境(Chrome 152+/Windows)会被忽略,
        文件直接落到浏览器默认下载目录 → 我们盯着的目录永远是空的("下载报表没有拿到数据")。
        """
        import glob as _glob
        found = []
        now = time.time()
        home = os.path.expanduser("~")
        for base in (os.path.join(home, "Downloads"), os.path.join(home, "下载"), home):
            if not base or not os.path.isdir(base):
                continue
            for pat in ("门店下载_*.xlsx", "*门店下载*.xlsx"):
                for p in _glob.glob(os.path.join(base, pat)):
                    try:
                        if now - os.path.getmtime(p) > max_age_min * 60:
                            continue
                        if os.path.getsize(p) < 1024:          # 空文件不要
                            continue
                        dst = os.path.join(ddir, os.path.basename(p))
                        if os.path.exists(dst):
                            continue
                        shutil.copy2(p, dst)
                        found.append(dst)
                    except Exception:
                        continue
        return found

    def _save_download(self, d, dl_dir):
        """把 Playwright download 事件落盘到目标目录(重名自动加序号)。

        **空文件守卫**: 平台"任务已提交但文件还没生成好"时会给 0 字节/极小文件,
        若当成下到了, 后面只会报"缺XX报表"这种看不懂的错。这里直接丢弃并让上层继续等。
        """
        try:
            name = d.suggested_filename or "report.xlsx"
            target = Path(dl_dir) / name
            i = 1
            while target.exists():
                target = Path(dl_dir) / f"{Path(name).stem}_{i}{Path(name).suffix}"
                i += 1
            d.save_as(str(target))
            try:
                size = target.stat().st_size
            except Exception:
                size = 0
            if size < 1024:      # 空/极小 → 平台还在生成, 不当成有效下载
                self.sink.log(f"⚠️ 下载到的文件是空的({size} 字节): {target.name} "
                              f"→ 丢弃并继续等待(平台可能还在生成)", level="warn")
                try:
                    target.unlink()
                except Exception:
                    pass
                return
            target = self._rename_canonical(target)
            self.sink.log(f"💾 已保存下载: {target.name} ({size // 1024}KB)")
        except Exception as e:
            self.sink.log(f"⚠️ 下载保存失败: {str(e)[:90]}", level="warn")

    def _rename_canonical(self, target):
        """按**内容表头签名**把下载文件改成规范名 淘宝_{类型}_{实际区间}_{平台原名}。

        为什么必须改: 平台原名里混着「下载时间戳」—— `门店下载_20260917至20260922_..._
        20260923164541.xlsx` 的 20260923 是**下载时间**(09-23 下载的), 不是报表日期;
        光按名字找文件会把这份 09-17~09-22 的旧文件当成 09-23 的报表, 报
        「报表内缺少这些日期的数据行」(2026-09-24 实测)。改名后按类型+区间精确命中。

        ⚠️ 区间取**平台原名里实际提交的那个**, 不取我们的请求区间: 2026-09-30 真机请求 09-28,
        平台却生成 09-29(下载页日期没生效) —— 若按请求区间命名, 就会把 09-29 的报表
        伪装成"09-28 的本次报表"(内容校验虽会兜住, 但文件名骗人)。
        """
        try:
            req_from, req_to = getattr(self, "_dl_range", ("", ""))
            kind = report_kind(str(target))       # 认不出(坏文件/异形表) → 保持原名
            if not kind:
                return target
            a, b = platform_range_in_name(target.name) or (req_from, req_to)
            if not a:
                return target
            if req_from and (a != req_from or b != (req_to or req_from)):
                self.sink.log(f"⚠️ 下回来的报表区间({a}~{b})与本次请求区间"
                              f"({req_from}~{req_to or req_from})不一致 —— 这份不会被用于填表"
                              f"(下载页日期可能没生效), 已按真实区间命名留档", level="warn")
            new = target.with_name(canonical_report_name(kind, a, b, target.name))
            if new.exists():
                return target
            target.rename(new)
            return new
        except Exception as e:
            self.sink.log(f"ℹ️ 规范名重命名跳过(不影响后续按内容选文件): {str(e)[:60]}",
                          level="warn")
            return target

    def _visible_labels(self, f, sel="li", limit=14):
        """页面上可见的候选项文案(失败时用来告诉用户/我们"这里到底有什么")。"""
        try:
            return f.evaluate("""(a) => {
              const [sel, lim] = a;
              const out = [];
              for (const el of document.querySelectorAll(sel)) {
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                const t = (el.innerText || '').trim();
                if (t && t.length < 30) out.push(t);
                if (out.length >= lim) break;
              }
              return out;
            }""", [sel, limit]) or []
        except Exception:
            return []

    def _manager_snapshot(self, f, limit=600):
        """下载管理页快照(任务行/状态): 定位"没生成"是提交没生效还是平台侧没出。"""
        try:
            txt = f.evaluate("() => document.body ? document.body.innerText : ''") or ""
        except Exception as e:
            return f"(读取失败: {str(e)[:60]})"
        return " ".join(str(txt).split())[:limit]

    def _submit_task(self, f, order_type):
        """选订单类型 + 勾字段 + 点「下载数据」。返回是否选中(False=页面没有该选项)。
        **必须精确匹配**: 曾用"包含"匹配把「全部」错选成「全部门店」→ 页面进入意外状态,
        后续 frame 全部失效(Target closed)。宁可选不到(告警+列选项), 也不许选错。"""
        labels = self._visible_labels(f, "li")
        picked = None
        for li in f.locator("li").all():
            try:
                if li.inner_text().strip() == order_type and li.is_visible():
                    li.click()
                    picked = order_type
                    break
            except Exception:
                pass
        if picked is None:
            # 账号视角不同(单店版可能没有「爆品团」这类业务) → 告警并列出真实选项, 不静默跳过
            self.sink.log(f"⚠️ 下载表单没有「{order_type}」选项, 跳过。页面上的选项: {labels}",
                          level="warn")
            return False
        time.sleep(1.5)
        n = f.evaluate("""() => {
          let c=0,u=0;
          for (const w of document.querySelectorAll('.ant-checkbox-wrapper')) {
            const r=w.getBoundingClientRect();
            if (!(r.width>0 && r.bottom>0)) continue;
            const own=w.innerText.trim();
            if (!own || own==='全选') continue;
            w.className.includes('checked') ? c++ : u++;
          }
          return {c,u};
        }""")
        if n["u"] > 0:
            f.evaluate("""() => {
              for (const w of document.querySelectorAll('.ant-checkbox-wrapper')) {
                const r=w.getBoundingClientRect();
                if (w.innerText.trim()==='全选' && r.width>0 && !w.className.includes('checked')) {
                  (w.closest('label')||w).click(); return;
                }
              }
            }""")
            time.sleep(1.5)
        f.locator("button:has-text('下载数据'):visible").first.click(timeout=8000)
        self.sink.log(f"📤 已提交任务: 订单类型={picked} (字段{n['c']}列)")
        return True

    def _goto_manager(self, f):
        el = f.get_by_text("下载管理", exact=True)
        for i in range(el.count()):
            if el.nth(i).is_visible():
                el.nth(i).click()
                time.sleep(3)
                return
        raise RuntimeError("找不到「下载管理」入口")

    def _wait_files(self, f, ddir, before, timeout_s, need=2):
        """文件落盘为准; 兜底对「成功」行点下载(幂等)。
        need: 期望份数(单店版可能没「爆品团」业务 → 只提交 1 份)。超时抛**带快照**的错误。"""
        clicked = set()
        end = time.time() + timeout_s
        got = []
        self.sink.step_start("download", "下载报表", f"任务已提交, 等待生成(期望{need}份)")
        while time.time() < end:
            time.sleep(4)
            new = [x for x in set(os.listdir(ddir)) - before
                   if x.endswith(".xlsx") and not x.startswith(".")]
            got = new
            if len(new) >= need:
                self.sink.step_ok("download", "下载报表", f"{len(new)} 份文件落盘")
                return new
            try:
                txt = f.evaluate("() => document.body.innerText")
            except Exception:
                continue
            for fn in [x for x in txt.split("\n") if x.endswith(".xlsx") and "门店下载" in x]:
                frag = fn.split("_")[-1].replace(".xlsx", "")
                if frag in clicked:
                    continue
                idx = txt.find(fn)
                if idx < 0 or "成功" not in txt[idx:idx + 120]:
                    continue
                r = f.evaluate("""(frag) => {
                  let best=null;
                  for (const el of document.querySelectorAll('*')) {
                    const t=el.innerText||'';
                    if (t.includes(frag) && t.includes('删除')) {
                      if (!best || el.textContent.length < best.textContent.length) best=el;
                    }
                  }
                  if (!best) return 'notfound';
                  for (const c of best.querySelectorAll('*')) {
                    if (c.innerText && c.innerText.trim()==='下载' && c.children.length===0) {
                      const rr=c.getBoundingClientRect();
                      if (rr.width>0) { c.click(); return 'clicked'; }
                    }
                  }
                  return 'nodlbtn';
                }""", frag)
                if r == "clicked":
                    clicked.add(frag)
                    self.sink.log(f"⬇️ 已点下载: {fn}")
                    time.sleep(3)
        # 超时前的两次补救/取证:
        # ① 下载事件计数 —— 0 次 = 浏览器侧压根没触发下载(落盘/环境问题); >0 次 = 触发了但
        #    文件被空文件守卫丢弃(平台还在生成)。2026-09-28 Windows 报「缺全部爆品团(已有:无)」
        #    时就是靠这个数字区分「平台慢」还是「我们收不到」。
        # ② 去浏览器默认下载目录找回本次文件(CDP 下载行为被忽略时文件会落到那里)。
        _ev = getattr(self, "_dl_events", 0)
        try:
            _stray = self._recover_stray_downloads(ddir)
        except Exception:
            _stray = []
        if _stray:
            self.sink.log(f"♻️ 在浏览器默认下载目录找到本次文件, 已复制进报表目录: "
                          + "、".join(os.path.basename(x) for x in _stray), level="warn")
            return [os.path.basename(x) for x in _stray]
        snap = self._manager_snapshot(f)
        self.sink.step_fail("download", "下载报表",
                            f"生成超时｜下载管理页: {snap[:180]}", "E_TASK_SLOW")
        raise RuntimeError(
            f"下载任务 {timeout_s}s 内没有产出文件（期望 {need} 份, 实际 {len(got)} 份; "
            f"期间收到下载事件 {_ev} 次）"
            f"｜下载管理页快照: {snap}"
            "｜判断依据: 事件 0 次=浏览器没触发下载(环境/落盘问题, 请把日志发我们); "
            "事件>0 次但文件都小于 1KB=平台还在生成(稍后重试); "
            "快照无任务行=提交没生效; 快照显示「失败」=平台侧拒绝(常见于区间超范围)。")

    # ---------- 流量 6 态 + 推广 ----------
        for _ in range(max_flip):
            # 当前面板头部的年月(可能双面板显示相邻两月)
            headers = f.evaluate("""() => {
              const out = [];
              for (const el of document.querySelectorAll('[class*="header"] [class*="super"], [class*="header"] span[class*="month"], [class*="header"] span[class*="year"]')) {
                const r = el.getBoundingClientRect();
                if (r.width > 0) out.push((el.innerText||'').trim());
              }
              return out;
            }""")
            header_txt = "".join(headers)
            # 找目标日期单元格(可见)
            cell = f.evaluate("""(date) => {
              for (const td of document.querySelectorAll('.ant-picker-cell td[title], td[title]')) {
                if (td.getAttribute('title') !== date) continue;
                const r = td.getBoundingClientRect();
                if (r.width > 10 && r.height > 10) {
                  return {x: r.x + r.width/2, y: r.y + r.height/2};
                }
              }
              return null;
            }""", date)
            if cell:
                # 真实鼠标点击(range 选择需要真实事件)
                self.page.mouse.click(cell["x"], cell["y"])
                time.sleep(0.8)
                return True
            # 目标月不在当前视图 → 判断往前还是往后翻
            try:
                cy, cm = int(_re.search(r"(\d{4})年(\d{1,2})月", header_txt).group(1)), int(_re.search(r"(\d{4})年(\d{1,2})月", header_txt).group(2))
            except Exception:
                break
            ty, tm = int(y), int(m)
            diff = (ty - cy) * 12 + (tm - cm)
            if diff == 0:
                # 同年月但单元格不可见 → 可能被禁用(out of range), 放弃
                break
            btn_sel = ".ant-picker-header-prev-btn" if diff < 0 else ".ant-picker-header-next-btn"
            try:
                f.locator(btn_sel).first.click(timeout=3000)
                time.sleep(0.8)
            except Exception:
                break
        self.sink.log(f"⚠️ 日历中未找到 {date} (翻页{flipped}次)")
        return False

    def capture_stats(self, date_from=None, date_to=None):
        """流量页6态(外卖/爆品团×全客/新/老客, 基准=前10%均值) + 推广。
        单日(date_from==date_to 或无区间): 一次抓取(自定义点单日/昨日)。
        区间: 逐日循环抓取(平台的"自定义"入口只支持单日, 点一下闭合), 返回 {日期: 各态数据}。"""
        f = self._open_stats()
        out = {}
        if date_from and date_to and date_from != date_to:
            # 区间模式: 逐日
            from datetime import datetime as _dt, timedelta as _td
            d0 = _dt.strptime(date_from, "%Y-%m-%d")
            d1 = _dt.strptime(date_to, "%Y-%m-%d")
            days = [(d0 + _td(days=i)).strftime("%Y-%m-%d") for i in range((d1 - d0).days + 1)]
            for i, d in enumerate(days):
                assert self._click_nav(f, "流量"), f"流量导航失败({d})"
                self._wait_text(f, "流量转化")
                assert self._pick_single_day(f, d), f"自定义选 {d} 失败"
                self._ensure_bench(f)   # 品牌版无商圈基准时沿用页面基准(返回False不失败)
                _st, _dims_ok = self._capture_states_checked(f)
                out[d] = dict(_st, customer_dims_ok=_dims_ok)
                self.sink.progress("capture", 15 + int(70 * (i + 1) / len(days)))
            # 推广(区间): 推广页有总消费区间口径, 取最后设置的单日逐日累计
            promo_sum = {"GG": 0.0, "GH": 0, "GI": 0}
            for d in days:
                assert self._click_nav(f, "推广"), "推广导航失败"
                self._wait_text(f, "推广概览")
                self._pick_single_day(f, d)
                pr = self._read_promo(f)
                for k in promo_sum:
                    promo_sum[k] += pr.get(k) or 0
            out["promo"] = {k: (round(v, 2) if k == "GG" else v) for k, v in promo_sum.items()}
            self.sink.step_ok("capture", "网页抓取",
                              f"区间逐日 {len(days)} 天 + 推广累计(消费{out['promo']['GG']})")
            return out
        # 单日模式(原有逻辑)
        assert self._click_nav(f, "流量"), "流量导航失败"
        self._wait_text(f, "流量转化")
        if date_from:
            assert self._pick_single_day(f, date_from), "自定义选单日失败"
        else:
            self._pick_yesterday(f)
        self._ensure_bench(f)   # 品牌版无商圈基准时沿用页面基准(返回False不失败)
        _states, _dims_ok = self._capture_states_checked(f)
        out.update(_states)
        out["customer_dims_ok"] = _dims_ok
        # 推广页(单日模式): 沿用当前自定义单日; 品牌版推广页若无数据则留空不失败
        assert self._click_nav(f, "推广"), "推广导航失败"
        self._wait_text(f, "推广概览")
        if date_from:
            self._pick_single_day(f, date_from)
        else:
            self._pick_yesterday(f)
        promo = {"GG": None, "GH": None, "GI": None}
        for _ in range(6):
            time.sleep(2.5)
            promo = f.evaluate("""() => {
              const t=document.body.innerText;
              const g=(kw)=>{const m=t.match(new RegExp(kw+'\\\\n([\\\\d,.]+)')); return m? +m[1].replace(/,/g,''):null;};
              return {"GG":g('总推广消费'),"GH":g('总曝光次数'),"GI":g('总进店次数')};
            }""")
            if promo.get("GG") is not None:
                break
        out["promo"] = promo
        self.sink.step_ok("capture", "网页抓取", f"6态+推广(消费{promo['GG']})")
        return out

    # ================= 单店(逐店)抓取 =================
    # 2026-10-01 真机核实(用户口径: 淘宝闪购也逐店抓, 只抓营业中的门店):
    #   ① 右上角 #shopSwitcher 是 cook-cascader 组织树: 账号 → 运营组织 → 门店;
    #      门店行 span.itemShopName__xxx 的 data-aspm-param 里带 status=营业中/已打烊/暂停营业,
    #      组织行是 status=可用 level_name=1 → 用 level_name=2 过滤出门店。
    #   ② 点门店行会被 div.tooltipMask__xxx 遮罩拦指针 → 必须 click(force=True)。
    #   ③ 切店后 URL 变成 /app/shop/<shop_id>/dashboard(每店独立 shop_id) ✓
    #   ④ 切店后流量页路由从 chain/business-analysis 变成 **single/business-analysis**,
    #      漏斗从「本店|顾客维度」变成「本店|商圈基准」, 基准下拉里有
    #      「商圈同行均值 / 商圈同行前10%均值」 ← 这就是商圈TOP10 的来源。
    SHOP_SWITCHER = "#shopSwitcher"

    def _shop_name(self, page):
        """右上角当前门店名(用来判断切店成不成功)。"""
        try:
            return page.evaluate("() => (document.querySelector('#shopSwitcher .shop-name__mxY5RVXaJn-Bcnuv2ByWd')||{}).innerText || ''")
        except Exception:
            return ""

    def _switcher_open(self, page):
        try:
            return page.locator(f"{self.SHOP_SWITCHER} [data-aspm-param*='status=']").first.is_visible()
        except Exception:
            return False

    def _open_switcher(self, page):
        """打开右上角门店下拉并展开所有「运营组织」节点。

        坑(2026-10-01 真机): 直接 force 点外层 #shopSwitcher 有时不触发(它在 tabindex=-1 的
        外层上, 真正吃点击的是里面的 [class*=clk-area]); 点空还可能是"点了一下又关掉"。
        → 先看点没点开(门店行是否可见), 没开就换内层 clk-area 点, 最多 4 轮。
        """
        for _ in range(5):
            if self._switcher_open(page):
                break
            for how in ("clk", "force", "mouse"):
                try:
                    if how == "clk":
                        page.locator(f"{self.SHOP_SWITCHER} [class*=clk-area]").first.click(timeout=3500)
                    elif how == "force":
                        page.locator(self.SHOP_SWITCHER).first.click(timeout=4000, force=True)
                    else:
                        box = page.locator(self.SHOP_SWITCHER).first.bounding_box()
                        if not box:
                            continue
                        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    # 等弹层(orgTreePopup)真的出现再往下走, 别靠 sleep 猜
                    page.wait_for_selector("#shopSwitcher [class*=orgTreePopup]",
                                           timeout=4000, state="visible")
                    break
                except Exception:
                    continue
            time.sleep(1.0)
        for _ in range(5):      # 展开全部「运营组织」节点
            ex = page.locator(f"{self.SHOP_SWITCHER} .cook-cascader-menu-item-expand")
            n = ex.count()
            if not n:
                break
            for i in range(n):
                try:
                    ex.nth(i).click(timeout=2500, force=True)
                    time.sleep(0.6)
                except Exception:
                    pass
            time.sleep(0.9)

    def list_stores(self, page=None):
        """列出门店 + 营业状态: [{"name","status","open"}] (open=status 是「营业中」)。"""
        page = page or self.page
        rows = []
        for attempt in range(3):
            self._open_switcher(page)
            rows = page.evaluate("""() => [...document.querySelectorAll('#shopSwitcher [data-aspm-param*="status="]')]
          .map(el => { const p = el.getAttribute('data-aspm-param') || '';
                       const lv = (p.match(/level_name=([0-9]+)/) || [])[1] || '';
                       const st = (p.match(/status=([^&^]+)/) || [])[1] || '';
                       return {name: (el.innerText || '').trim(), status: st, level: lv}; })
          .filter(r => r.name && r.level === '2')""")
            if rows:
                break
            self.sink.log(f"⚠️ 门店下拉没读出内容(第{attempt + 1}次), 重试", level="warn")
            page.keyboard.press("Escape")
            time.sleep(1.5)
        page.keyboard.press("Escape")
        time.sleep(0.5)
        return [{"name": r["name"], "status": r["status"], "open": r["status"] == "营业中"} for r in rows]

    def switch_shop(self, name, page=None):
        """切到指定门店; 返回 shop_id(URL 里的) 或 ''。"""
        page = page or self.page
        tgt = None
        for _ in range(3):
            self._open_switcher(page)
            for probe in (name, name.strip("示例柠檬茶()"), name[4:8]):
                if not probe:
                    continue
                loc = page.locator(f"{self.SHOP_SWITCHER} >> text={probe}")
                if loc.count() and loc.first.is_visible():
                    tgt = loc.first
                    break
            if tgt is not None:
                break
            page.keyboard.press("Escape")
            time.sleep(1.0)
        if tgt is None:
            self.sink.log(f"⚠️ 门店列表里找不到/点不到「{name}」", level="warn")
            page.keyboard.press("Escape")
            return ""
        try:
            tgt.click(timeout=6000, force=True)     # tooltipMask 遮罩 → force
        except Exception as e:
            self.sink.log(f"⚠️ 点门店「{name}」失败: {str(e)[:60]}", level="warn")
            page.keyboard.press("Escape")
            return ""
        time.sleep(2.5)
        for _ in range(2):      # 切店成功与否用右上角门店名验收, 不一致就重试
            cur = self._shop_name(page)
            if name in (cur or ""):
                break
            self.sink.log(f"⚠️ 切店后右上角是「{cur}」≠「{name}」→ 重试", level="warn")
            self._open_switcher(page)
            loc = page.locator(f"{self.SHOP_SWITCHER} >> text={name}")
            if loc.count():
                try:
                    loc.first.click(timeout=6000, force=True)
                except Exception:
                    pass
            time.sleep(3)
        cur = self._shop_name(page)
        m = re.search(r"/app/shop/(\d+)/", page.url or "")
        sid = m.group(1) if m else ""
        if name not in (cur or ""):
            self.sink.log(f"⚠️ 切店失败: 右上角仍是「{cur}」", level="warn")
            return ""
        return sid

    def _pick_bench(self, f, want="商圈同行前10%均值"):
        """单店流量页: 把漏斗基准切成「商圈同行前10%均值」。返回是否切成功。

        真机(2026-10-01): 基准是个 ant-dropdown-trigger, 点开后选项
        ['商圈同行均值', '商圈同行前10%均值']。
        """
        cur = (self._parse_funnel(f).get("cust") or "").strip()
        if want in cur:
            return True
        for label in (cur, "商圈同行均值", "比上周同时段"):
            if not label:
                continue
            try:
                loc = f.locator(f'text="{label}"')
                if not loc.count():
                    continue
                loc.first.click(timeout=5000)
                time.sleep(1.5)
                opt = f.locator(f'.ant-select-dropdown:not(.ant-select-dropdown-hidden) >> text="{want}"')
                if not opt.count():
                    opt = f.locator(f'text="{want}"')
                if opt.count():
                    opt.first.click(timeout=5000)
                    time.sleep(2.5)
                    return want in (self._parse_funnel(f).get("cust") or "")
                f.keyboard.press("Escape")
                time.sleep(0.6)
            except Exception as e:
                self.sink.log(f"⚠️ 切基准「{want}」失败({label}): {str(e)[:60]}", level="warn")
        return False

    def _set_store_date(self, f, date):
        """单店流量页选日期。

        2026-10-01 真机: 单店页(路由 single/business-analysis)用自定义日历**没生效**
        (趋势区还显示"比上一周期", 当天数字全是 0) → 最稳的是预设 radio「昨日」。
        所以: 目标日期 = 昨天就点「昨日」; 其它日期才走自定义日历。
        """
        yest = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if date == yest:
            btn = self._date_radio(f, "昨日")
            if btn is not None:
                try:
                    btn.click(timeout=5000)
                    time.sleep(3)
                    self.sink.log(f"📅 单店页已切「昨日」({date})")
                    return True
                except Exception as e:
                    self.sink.log(f"⚠️ 点「昨日」失败: {str(e)[:60]}", level="warn")
        return self._pick_single_day(f, date)

    def _wait_funnel_numbers(self, f, timeout_s=24, poll=1.5):
        """等漏斗数字加载出来(单店页是异步的; 拿到就返回), 超时就返回最后一次读到的。"""
        nums = {}
        for _ in range(max(1, int(timeout_s / poll))):
            nums = self._parse_funnel(f)["nums"]
            if nums.get("exp_shop"):
                return nums
            time.sleep(poll)
        return nums

    def capture_per_store(self, stores, date, page=None, bench="商圈同行前10%均值"):
        """逐店抓取(只抓营业中的门店) → {店名: {整体/新客/老客 + top10(商圈前10%)}}。

        每店: 切店 → 经营分析→流量 → 选日期 → 基准=商圈同行前10%均值 → 读
        「本店」列(网站口径, 覆盖报表减法) 与「基准」列(← 商圈TOP 静态列的数据源)。
        顾客维度(新客/老客)在单店页不一定有 → 没有就只填整体, 新客/老客仍走报表逐店口径。
        """
        page = page or self.page
        out = {}
        for i, st in enumerate(stores):
            name = st["name"] if isinstance(st, dict) else str(st)
            if not (st.get("open") if isinstance(st, dict) else True):
                self.sink.log(f"⏭️ 跳过(非营业中): {name}")
                continue
            sid = self.switch_shop(name, page)
            if not sid and name not in str(self._shop_name(page)):
                self.sink.log(f"⏭️ 跳过(切店失败): {name}", level="warn")
                continue
            self.sink.log(f"📍 切到门店: {name} (shop {sid or '?'}) [{i + 1}/{len(stores)}]")
            try:
                f = self._open_stats()
                self._click_nav(f, "流量")
                self._set_store_date(f, date)
                self._ensure_bench(f)
                per, top10, zb_top, bench_label, ftype_ok = None, {}, {}, "", False
                seg = {}    # {类型: {新客/老客: {top10: {...}}}} → 模板的新客/老客 TOP 列
                for ftype in ("外卖", "爆品团"):
                    try:
                        ftype_ok = bool(self._pick_ftype(f, ftype))
                    except Exception:
                        ftype_ok = False
                    self._pick_bench(f, bench)
                    nums = self._wait_funnel_numbers(f)
                    cust = (self._parse_funnel(f).get("cust") or "").strip()
                    ok = bench in cust
                    pack = {"exp": nums.get("exp_shop"), "ent": nums.get("ent_shop"), "ord": nums.get("ord_shop")}
                    pack["ent_rate"] = (round(pack["ent"] / pack["exp"], 6)
                                        if pack["exp"] and pack["ent"] is not None else None)
                    pack["ord_rate"] = (round(pack["ord"] / pack["ent"], 6)
                                        if pack["ent"] and pack["ord"] is not None else None)
                    tk = {"exp": nums.get("exp_top"), "ent": nums.get("ent_top"), "ord": nums.get("ord_top")}
                    tk["ent_rate"] = (round(tk["ent"] / tk["exp"], 6)
                                      if tk["exp"] and tk["ent"] is not None else None)
                    tk["ord_rate"] = (round(tk["ord"] / tk["ent"], 6)
                                      if tk["ent"] and tk["ord"] is not None else None)
                    self.sink.log(f"   ↳ {name} [{ftype}] 本店 {pack['exp']}/{pack['ent']}/{pack['ord']} ｜ "
                                  f"基准({cust or '?'}) {tk['exp']}/{tk['ent']}/{tk['ord']}"
                                  f"{'' if ok else ' ⚠️ 基准不是前10%, TOP列本次留空'}")
                    if ftype == "外卖":
                        # ⚠️ 口径: 单店页默认「全部」= 主站+爆品团(真机: 铁道 8,059 = 报表主站 5,315 + 爆品团 2,744);
                        #    只有确实切到「外卖」时, 本店列才是主站口径 → 才允许覆盖报表减法。
                        if ftype_ok:
                            per = pack
                        bench_label = cust
                        top10 = tk if ok else {}
                    else:
                        zb_top = tk if ok else {}
                    # 顾客维度(新客/老客) + 同一基准 → 模板的「新客/老客 TOP」列(ED/EG/EJ/EM/EP/ES、FP/FS/FV/FY/GB/GE)
                    if ok:
                        for cname in ("新客", "老客"):
                            try:
                                if not self._pick_cust(f, cname):
                                    continue
                                self._pick_bench(f, bench)
                                n2 = self._wait_funnel_numbers(f, timeout_s=14)
                                # 基准验收(2026-10-01 真机): 单店页布局和连锁不同, 漏斗文本解析不出 cust
                                # → 拿"页面上确实显示着这个基准标签"当证据(下拉当前选中项就是它)。
                                c2 = (self._parse_funnel(f).get("cust") or "").strip()
                                shown = bench in c2 or bool(f.evaluate(
                                    "(b) => [...document.querySelectorAll('*')].some("
                                    "el => ((el.innerText || '').trim()) === b)", bench))
                                if not shown:
                                    continue
                                t2 = {"exp": n2.get("exp_top"), "ent": n2.get("ent_top"), "ord": n2.get("ord_top")}
                                t2["ent_rate"] = (round(t2["ent"] / t2["exp"], 6)
                                                  if t2["exp"] and t2["ent"] is not None else None)
                                t2["ord_rate"] = (round(t2["ord"] / t2["ent"], 6)
                                                  if t2["ent"] and t2["ord"] is not None else None)
                                seg.setdefault(ftype, {})
                                seg[ftype][cname] = {"top10": t2}
                                self.sink.log(f"   ↳ {name} [{ftype}·{cname}] 基准 {t2['exp']}/{t2['ent']}/{t2['ord']}")
                            except Exception as e:
                                self.sink.log(f"   ⚠️ {name} [{ftype}·{cname}] 维度读取失败: {str(e)[:50]}", level="warn")
                        # 顾客维度读完后切回全部顾客, 免得影响下一次循环
                        try:
                            self._pick_cust(f, "全部顾客")
                        except Exception:
                            pass
                out[name] = {"外卖_全部顾客": {"shop": per or {}, "top10": top10},
                             "爆品团_全部顾客": {"shop": {}, "top10": zb_top},
                             "segments": seg,
                             "customer_dims_ok": False, "bench": bench_label,
                             "bench_ok": bool(top10), "ftype_ok": ftype_ok, "shop_id": sid}
            except Exception as e:
                self.sink.log(f"⚠️ 门店「{name}」抓取失败: {str(e)[:80]}", level="warn")
        return out

    def _capture_states_checked(self, f):
        """抓 6 态 + **自校验**顾客维度是否真生效 → (states, dims_ok)。

        2026-09-30 真机核实: 漏斗两列 = 「本店(全部顾客)」 vs 「顾客下拉所选(新客/老客)」。
        顾客下拉点不开时, 读到的新客/老客列与整体完全相同 → 旧代码把整体值写进了新客/老客列。
        校验: 新客/老客两组数字都要有、且**互不相同、也不等于整体**, 否则 dims_ok=False,
        下游改用**报表逐店口径**(全部−爆品团) —— 宁可不写, 也不写错。
        """
        states = self._capture_all_states(f)
        bad = []
        for ftype in ("外卖", "爆品团"):
            nk = (states.get(f"{ftype}_新客") or {}).get("shop") or {}
            lk = (states.get(f"{ftype}_老客") or {}).get("shop") or {}
            hk = (states.get(f"{ftype}_全部顾客") or {}).get("shop") or {}
            if not nk.get("exp") or not lk.get("exp"):
                bad.append(f"{ftype}(维度缺失)")
            elif nk.get("exp") == lk.get("exp"):
                bad.append(f"{ftype}(新客=老客, 下拉未生效)")
            elif hk.get("exp") and nk.get("exp") == hk.get("exp"):
                bad.append(f"{ftype}(新客=整体)")
        if bad:
            self.sink.log(f"⚠️ 顾客维度自校验未通过: {'、'.join(bad)} → 新客/老客列改用"
                          f"报表逐店口径(全部−爆品团), 不用网页值", level="warn")
            return states, False
        return states, True

    def _capture_all_states(self, f):
        """抓 6 态: (外卖|爆品团) × (整体|新客|老客)。

        页面口径(2026-09-30 真机用报表数字反推核实):
          漏斗 = 「本店(全部顾客)」 vs 「顾客下拉所选」; 整页**没有**商圈同行/前10% 列。
          铁证: 流量类型=全部 两列 = 74,383 / 64,454, 而报表 Σ曝光 = 74,383、Σ新客曝光 = 64,454 ✓ 全等;
                爆品团 两列 = 26,302 / 23,886 = Σ爆品团曝光 / Σ爆品团新客曝光 ✓。
        所以: 整体 ← 本店列; 新客/老客 ← 把顾客下拉切过去后的对比列。
        转化率用**人数相除自算**(不依赖页面上那串 % 的顺序); "top10" 一律留空(品牌版取不到商圈TOP)。
        """
        out = {}

        def pack(exp, ent, ord_):
            return {"exp": exp, "ent": ent, "ord": ord_,
                    "ent_rate": round(ent / exp, 6) if exp and ent is not None else None,
                    "ord_rate": round(ord_ / ent, 6) if ent and ord_ is not None else None}

        for ftype in ("外卖", "爆品团"):
            assert self._pick_ftype(f, ftype), f"流量类型切{ftype}失败"
            for cust in ("新客", "老客"):
                if not self._pick_cust(f, cust):
                    self.sink.log(f"⚠️ {ftype} 顾客维度切「{cust}」失败 → 本次该维度留空", level="warn")
                    continue
                time.sleep(1.0)
                num = self._parse_funnel(f)["nums"]
                out.setdefault(f"{ftype}_全部顾客", {
                    "shop": pack(num["exp_shop"], num["ent_shop"], num["ord_shop"]), "top10": {}})
                out[f"{ftype}_{cust}"] = {
                    "shop": pack(num["exp_top"], num["ent_top"], num["ord_top"]), "top10": {}}
        return out

    def _pick_single_day(self, f, date):
        """把流量页日期切成「date 单日」, 返回是否真的切成。

        2026-09-24 真机实测(品牌版 melody-stats-next 改版后的控件):
        ① 触发点 = 流量页那一排的 `.ant-radio-button-wrapper`「自定义」, 必须用 **locator**
           点(自动换算 iframe 偏移)。用 page.mouse.click(frame 内部量出的坐标) 会点到
           顶层页面别处 —— 这正是历史「自定义选单日失败」的根因。
        ② 它是 antd **区间**选择器: 点目标日 1 次只设起点, **同一格点 2 次**才补齐区间,
           平台显示「已选时间：09-21至09-21 (1日)」才算单日落定。
        ③ 「自定义」已是选中态时再点不会弹层 → 先点预设(昨日)打破选中态再点自定义。
        验收: 轮询「已选时间」文本, 不用固定 sleep。
        """
        want = (date[5:], date[5:])
        try:
            if self._read_selected_range(f) == want:   # 幂等: 已经是这一天就不折腾
                return True
            if not self._open_calendar(f):
                return False
            if not self._click_calendar_date(f, date):
                return False
            if self._wait_selected_range(f, want, timeout_s=20):
                self.sink.log(f"📅 已选 {date}")
                return True
            self.sink.log(f"⚠️ 选单日 {date} 后已选时间未变(当前 {self._read_selected_range(f)})")
            return False
        except Exception as e:
            self.sink.log(f"⚠️ 选单日失败: {str(e)[:80]}")
            return False

    def _wait_selected_range(self, f, want, timeout_s=20, poll=0.8):
        """等「已选时间」变成 want=(MM-DD, MM-DD)。按轮次计时(不用 time.time, 便于测试替身)。"""
        for _ in range(max(1, int(timeout_s / poll))):
            try:
                if self._read_selected_range(f) == want:
                    return True
            except Exception:
                pass
            time.sleep(poll)
        return False

    def _date_radio(self, f, text):
        """流量页那一排日期预设 radio(今日实时/昨日/近7日/近30日/自定义/按周/按月)。
        优先限定在 .sycm-local-date-radio 里(页面上别处也有同名「自定义」), 限定不到再全局找。"""
        for sel in (f".sycm-local-date-radio .ant-radio-button-wrapper",
                    ".ant-radio-button-wrapper"):
            try:
                loc = f.locator(sel, has_text=text)
                if loc.count():
                    return loc.first
            except Exception:
                continue
        return None

    def _wait_calendar(self, f, timeout_s=8, poll=0.4):
        for _ in range(max(1, int(timeout_s / poll))):
            try:
                if self._calendar_open(f):
                    return True
            except Exception:
                pass
            time.sleep(poll)
        return False

    def _open_calendar(self, f, tries=3):
        """打开「自定义」日历弹层(幂等): 已开直接返回 True。返回 False 时调用方必须报错,
        绝不能继续按「已选中某天」往下走(否则会把别的日期的数据写进客户表)。"""
        if self._calendar_open(f):
            return True
        for i in range(tries):
            try:
                if i:   # 「自定义」已选中时再点不会弹层 → 先切到预设打破选中态
                    y = self._date_radio(f, "昨日")
                    if y:
                        y.click(timeout=6000)
                        time.sleep(0.8)
                btn = self._date_radio(f, "自定义")
                if not btn:
                    self.sink.log("⚠️ 找不到日期控件里的「自定义」按钮")
                    return False
                btn.click(timeout=6000)
            except Exception as e:
                self.sink.log(f"⚠️ 打开自定义日历第{i + 1}次失败: {str(e)[:70]}")
                continue
            if self._wait_calendar(f, timeout_s=8):
                return True
        self.sink.log("⚠️ 自定义日历打不开(点了「自定义」但日期面板没渲染出来)")
        return False

    def _read_promo(self, f):
        try:
            return f.evaluate("""() => {
              const t = document.body.innerText;
              const g = (kw) => { const m = t.match(new RegExp(kw + '\\n([\\d,.]+)')); return m ? +m[1].replace(/,/g, '') : null; };
              return {"GG": g('总推广消费'), "GH": g('总曝光次数'), "GI": g('总进店次数')};
            }""")
        except Exception:
            return {"GG": None, "GH": None, "GI": None}

    def _find_stats_frame(self, need_text="经营分析"):
        """内容优先: 在所有页面所有 frame 中找经营分析页 frame(URL 不可信——
        hash 路由后 URL 与内容可能不对应, 2026-09-17 实测)。
        区分标准(2026-09-20 品牌版实测):
        ① 主页面外壳菜单也含「经营分析」四字, 会误命中 → 须含 生意参谋 或导航 li;
        ② 视图切换会留隐藏的旧 frame 实例(DOM 在、class 活、宽 0) → 优先可见者。"""
        best = None
        best_visible = None
        for pg in self.ctx.pages:
            for fr in pg.frames:
                try:
                    t = fr.evaluate("() => document.body ? document.body.innerText : ''")
                except Exception:
                    continue
                if need_text not in t or len(t) <= 100:
                    continue
                is_sycm = "生意参谋" in t
                if not is_sycm:
                    try:
                        navs = fr.evaluate(
                            "() => [...document.querySelectorAll('ul li')].map(li => (li.innerText||'').trim())")
                    except Exception:
                        navs = []
                    is_sycm = any(n in ("流量", "推广", "总览", "订单", "营销") for n in navs)
                if not is_sycm:
                    continue
                try:
                    vis = fr.evaluate(
                        "() => { const b = document.body.getBoundingClientRect(); return b.width > 50 && b.height > 50; }")
                except Exception:
                    vis = False
                if not vis:
                    # 隐藏实例(DOM 残留): 不复用 —— _open_stats 会重新点入口让页面回到经营分析
                    continue
                best_visible = best_visible or fr
                best = best or fr
        return best_visible or best

    def _open_stats(self, rounds=3):
        """切到经营分析: 主页面点「经营分析」→ 等待 frame 就绪; 失败重试。
        品牌版(demo_account)菜单: 经营分析藏在 数据中心 子菜单下(收起态不可见) → 两跳路径。
        单店版: 左侧菜单直接有「经营分析」。"""
        self._dismiss_popups()
        target = self.nav_override or "经营分析"
        for rnd in range(rounds):
            # 已就绪? (上次任务遗留的可见 frame) 直接用
            f0 = self._find_stats_frame()
            if f0:
                return f0
            # 找可见的「经营分析」入口(主页面文本或左侧菜单)点击
            clicked = self.page.evaluate("""(target) => {
              for (const el of document.querySelectorAll('a, li, div, span')) {
                const t = (el.innerText || '').trim();
                if (t === target) {
                  const r = el.getBoundingClientRect();
                  if (r.width > 0 && r.height > 0) { el.click(); return true; }
                }
              }
              return false;
            }""", target)
            if not clicked:
                # 品牌版两跳: 数据中心 → 经营分析(子菜单收起时「经营分析」不可见)
                clicked = self.page.evaluate("""(target) => {
                  for (const parent of ['数据中心', '数据']) {
                    for (const el of document.querySelectorAll('a, li, div, span')) {
                      const t = (el.innerText || '').trim();
                      if (t !== parent) continue;
                      const r = el.getBoundingClientRect();
                      if (r.width <= 0 || r.height <= 0 || r.x > 240) continue;
                      el.click();
                      return true;
                    }
                  }
                  return false;
                }""", target)
                if clicked:
                    time.sleep(2)   # 子菜单展开
                    clicked = self.page.evaluate("""(target) => {
                      for (const el of document.querySelectorAll('a, li, div, span')) {
                        const t = (el.innerText || '').trim();
                        if (t === target) {
                          const r = el.getBoundingClientRect();
                          if (r.width > 0 && r.height > 0 && r.x > 80) { el.click(); return true; }
                        }
                      }
                      return false;
                    }""", target)
            if not clicked:
                self.sink.log(f"[经营分析] 未找到入口, 重试{rnd+1}/3")
                time.sleep(3)
                continue
            # 等 frame 就绪(最多 20s)
            for _ in range(10):
                time.sleep(2)
                f0 = self._find_stats_frame()
                if f0:
                    return f0
            self.sink.log(f"[经营分析] 点击后 frame 未就绪, 重试{rnd+1}/3")
        raise RuntimeError("经营分析 frame 未就绪(3轮重试)")

    def _click_nav(self, f, name):
        return f.evaluate("""(name) => {
          for (const li of document.querySelectorAll('ul li')) {
            const t=(li.innerText||'').trim();
            if (t===name || t.startsWith(name)) {
              const r=li.getBoundingClientRect();
              if (r.width>0) { li.click(); return true; }
            }
          }
          return false;
        }""", name)

    def _wait_text(self, f, kw, timeout=25):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if kw in f.evaluate("() => document.body.innerText"):
                    return True
            except Exception:
                pass
            time.sleep(2)
        return False

    def _pick_yesterday(self, f):
        for el in f.locator("text=昨日").all():
            try:
                bb = el.bounding_box()
                if bb and bb["width"] < 80 and bb["y"] < 280:
                    el.click()
                    time.sleep(3.5)
                    return True
            except Exception:
                pass
        return False

    def _read_selected_range(self, f):
        """读页面「已选时间」文本。兼容两种平台文案:
        单店版: '已选时间：09-14至09-14'; 品牌版: '已选时间：09-19 周六'(单日带星期)"""
        import re as _re
        try:
            txt = f.evaluate("() => document.body.innerText")
            m = _re.search(r"已选时间[：:]?\s*([\d]{2}-[\d]{2})至([\d]{2}-[\d]{2})", txt)
            if m:
                return m.group(1), m.group(2)
            # 品牌版单日: 09-19 周六 (紧跟"已选时间"才认, 防误匹配页内其它日期)
            m = _re.search(r"已选时间[：:]?\s*([\d]{2}-[\d]{2})\s*(?:周[一二三四五六日])?", txt)
            if m:
                return m.group(1), m.group(1)
        except Exception:
            pass
        return None

    def _pick_custom_range(self, f, date_from, date_to):
        """经营分析时间=「自定义」: ant-picker-range 双面板。
        交互: 打开面板时上次 range 仍激活(起点已设) → 第一次点击被当作"终点"而闭合。
        策略: 点一个"预备日期"(区间首日)闭合旧range → 重开面板(此时无激活起点) →
              依次点 起点→终点 完成 range。全程读「已选时间」验收。"""
        def mm(d):
            return d[5:]
        try:
            def open_panel():
                if self._calendar_open(f):   # 已开则不点(toggle)
                    return True
                for el in f.locator("text=自定义").all():
                    bb = el.bounding_box()
                    if bb and bb["width"] < 90 and bb["y"] < 300:
                        el.click()
                        time.sleep(1.5)
                        return True
                return False

            def read_range():
                return self._read_selected_range(f)

            cur = read_range()
            want = (mm(date_from), mm(date_to))
            self.sink.log(f"📅 当前已选: {cur}  目标: {want[0]}~{want[1]}")
            if cur == want:
                return True

            for round_ in range(3):
                open_panel()
                if not self._calendar_open(f):
                    open_panel()
                # 第1次点击: 区间首日(重置旧range)
                self._click_calendar_date(f, date_from)
                time.sleep(1)
                if not self._calendar_open(f):
                    open_panel()
                # 第2次: 起点
                self._click_calendar_date(f, date_from)
                time.sleep(0.8)
                if not self._calendar_open(f):
                    open_panel()
                # 第3次: 终点
                self._click_calendar_date(f, date_to)
                time.sleep(3)
                cur = read_range()
                self.sink.log(f"  第{round_+1}轮 → 已选: {cur}")
                if cur == want:
                    break
            if cur == want:
                self.sink.log(f"✅ 区间已生效 {cur[0]}~{cur[1]}")
                return True
            self.sink.log(f"⚠️ 区间未达目标(当前 {cur}), 数据按当前区间口径返回")
            return False
        except Exception as e:
            self.sink.log(f"⚠️ 自定义区间失败: {str(e)[:80]}")
            return False


    def _calendar_frame(self):
        """返回「打开状态」的日期弹层所在 frame。
        判据 = 真的可见的 .ant-picker-dropdown 里有日期单元格。旧写法全局数 td[title]>20,
        会被页面里别的表格误命中(改版后页面上到处是 td[title]), 必须限定在 picker 弹层内。"""
        for pg in self.ctx.pages:
            for fr in pg.frames:
                try:
                    n = fr.evaluate("""() => {
                      let cells = 0;
                      for (const dd of document.querySelectorAll('.ant-picker-dropdown')) {
                        const r = dd.getBoundingClientRect();
                        if (r.width < 50 || r.height < 50) continue;
                        cells += dd.querySelectorAll('td[title]').length;
                      }
                      return cells;
                    }""")
                    if n > 10:
                        return fr
                except Exception:
                    continue
        return None

    def _calendar_open(self, f=None):
        """日历面板真的开着 = 任一 frame 有可见的日期单元格"""
        return self._calendar_frame() is not None

    def _click_calendar_date(self, f, date):
        """在日历(自动定位所在 frame)中点 title=date 单元格。
        主世界原生 el.click()——Playwright hit-target 预检在双面板 range picker 上
        会误报遮挡(2026-09-17 实测)。跨月先翻页。"""
        import re as _re
        y, m, _ = date.split("-")
        for _ in range(6):   # 最多翻 6 页
            cf = self._calendar_frame()
            if not cf:
                self.sink.log("⚠️ 日历面板不存在")
                return False
            clicked = cf.evaluate("""(date) => {
              for (const td of document.querySelectorAll('td[title]')) {
                if (td.getAttribute('title') !== date) continue;
                const r = td.getBoundingClientRect();
                if (r.width > 10 && r.height > 10) {
                  const inner = td.querySelector('.ant-picker-cell-inner') || td;
                  inner.click();
                  return true;
                }
              }
              return false;
            }""", date)
            if clicked:
                time.sleep(0.8)
                return True
            headers = cf.evaluate("""() => {
              const out = [];
              for (const el of document.querySelectorAll('[class*="header"] span')) {
                const r = el.getBoundingClientRect();
                if (r.width > 0) out.push((el.innerText||'').trim());
              }
              return out;
            }""")
            m1 = _re.search(r"(\d{4})年(\d{1,2})月", "".join(headers))
            if not m1:
                break
            cy, cm = int(m1.group(1)), int(m1.group(2))
            ty, tm = int(y), int(m)
            diff = (ty - cy) * 12 + (tm - cm)
            if diff == 0:
                break
            btn_sel = ".ant-picker-header-prev-btn" if diff < 0 else ".ant-picker-header-next-btn"
            try:
                cf.locator(btn_sel).first.click(timeout=3000)
                time.sleep(0.8)
            except Exception:
                break
        self.sink.log(f"⚠️ 日历未找到 {date}")
        return False

    def _calendar_frame(self):
        """返回「打开状态」的日期弹层所在 frame。
        判据 = 真的可见的 .ant-picker-dropdown 里有日期单元格。旧写法全局数 td[title]>20,
        会被页面里别的表格误命中(改版后页面上到处是 td[title]), 必须限定在 picker 弹层内。"""
        for pg in self.ctx.pages:
            for fr in pg.frames:
                try:
                    n = fr.evaluate("""() => {
                      let cells = 0;
                      for (const dd of document.querySelectorAll('.ant-picker-dropdown')) {
                        const r = dd.getBoundingClientRect();
                        if (r.width < 50 || r.height < 50) continue;
                        cells += dd.querySelectorAll('td[title]').length;
                      }
                      return cells;
                    }""")
                    if n > 10:
                        return fr
                except Exception:
                    continue
        return None

    def _calendar_open(self, f=None):
        """日历面板真的开着 = 任一 frame 有可见的日期单元格"""
        return self._calendar_frame() is not None

    def _read_selected_range(self, f):
        """读页面「已选时间」文本。兼容两种平台文案:
        单店版: '已选时间：09-14至09-14'; 品牌版: '已选时间：09-19 周六'(单日带星期)"""
        import re as _re
        try:
            txt = f.evaluate("() => document.body.innerText")
            m = _re.search(r"已选时间[：:]?\s*([\d]{2}-[\d]{2})至([\d]{2}-[\d]{2})", txt)
            if m:
                return m.group(1), m.group(2)
            # 品牌版单日: 09-19 周六 (紧跟"已选时间"才认, 防误匹配页内其它日期)
            m = _re.search(r"已选时间[：:]?\s*([\d]{2}-[\d]{2})\s*(?:周[一二三四五六日])?", txt)
            if m:
                return m.group(1), m.group(1)
        except Exception:
            pass
        return None

    def _pick_custom_range(self, f, date_from, date_to):
        """经营分析时间=「自定义」: ant-picker-range 双面板。
        交互: 打开面板时上次 range 仍激活(起点已设) → 第一次点击被当作"终点"而闭合。
        策略: 点一个"预备日期"(区间首日)闭合旧range → 重开面板(此时无激活起点) →
              依次点 起点→终点 完成 range。全程读「已选时间」验收。"""
        def mm(d):
            return d[5:]
        try:
            def open_panel():
                if self._calendar_open(f):   # 已开则不点(toggle)
                    return True
                for el in f.locator("text=自定义").all():
                    bb = el.bounding_box()
                    if bb and bb["width"] < 90 and bb["y"] < 300:
                        el.click()
                        time.sleep(1.5)
                        return True
                return False

            def read_range():
                return self._read_selected_range(f)

            cur = read_range()
            want = (mm(date_from), mm(date_to))
            self.sink.log(f"📅 当前已选: {cur}  目标: {want[0]}~{want[1]}")
            if cur == want:
                return True

            for round_ in range(3):
                open_panel()
                if not self._calendar_open(f):
                    open_panel()
                # 第1次点击: 区间首日(重置旧range)
                self._click_calendar_date(f, date_from)
                time.sleep(1)
                if not self._calendar_open(f):
                    open_panel()
                # 第2次: 起点
                self._click_calendar_date(f, date_from)
                time.sleep(0.8)
                if not self._calendar_open(f):
                    open_panel()
                # 第3次: 终点
                self._click_calendar_date(f, date_to)
                time.sleep(3)
                cur = read_range()
                self.sink.log(f"  第{round_+1}轮 → 已选: {cur}")
                if cur == want:
                    break
            if cur == want:
                self.sink.log(f"✅ 区间已生效 {cur[0]}~{cur[1]}")
                return True
            self.sink.log(f"⚠️ 区间未达目标(当前 {cur}), 数据按当前区间口径返回")
            return False
        except Exception as e:
            self.sink.log(f"⚠️ 自定义区间失败: {str(e)[:80]}")
            return False


    def _calendar_frame(self):
        """返回「打开状态」的日期弹层所在 frame。
        判据 = 真的可见的 .ant-picker-dropdown 里有日期单元格。旧写法全局数 td[title]>20,
        会被页面里别的表格误命中(改版后页面上到处是 td[title]), 必须限定在 picker 弹层内。"""
        for pg in self.ctx.pages:
            for fr in pg.frames:
                try:
                    n = fr.evaluate("""() => {
                      let cells = 0;
                      for (const dd of document.querySelectorAll('.ant-picker-dropdown')) {
                        const r = dd.getBoundingClientRect();
                        if (r.width < 50 || r.height < 50) continue;
                        cells += dd.querySelectorAll('td[title]').length;
                      }
                      return cells;
                    }""")
                    if n > 10:
                        return fr
                except Exception:
                    continue
        return None

    def _calendar_open(self, f=None):
        """日历面板真的开着 = 任一 frame 有可见的日期单元格"""
        return self._calendar_frame() is not None

    def _click_calendar_date(self, f, date, max_flip=6):
        """在 ant-picker 日历点 title=date 单元格。
        用主世界原生 el.click()——Playwright hit-target 预检在双面板 range picker 上
        会误报 'table intercepts pointer events'(2026-09-17 实测), 原生点击无此问题。
        不在当前视图则按年月差翻页。"""
        import re as _re
        y, m, _ = date.split("-")
        flipped = 0
        for _ in range(max_flip):
            clicked = f.evaluate("""(date) => {
              for (const td of document.querySelectorAll('td[title]')) {
                if (td.getAttribute('title') !== date) continue;
                const r = td.getBoundingClientRect();
                if (r.width > 10 && r.height > 10) {
                  const inner = td.querySelector('.ant-picker-cell-inner') || td;
                  inner.click();
                  return true;
                }
              }
              return false;
            }""", date)
            if clicked:
                time.sleep(0.8)
                return True
            headers = f.evaluate("""() => {
              const out = [];
              for (const el of document.querySelectorAll('[class*="header"] span')) {
                const r = el.getBoundingClientRect();
                if (r.width > 0) out.push((el.innerText||'').trim());
              }
              return out;
            }""")
            m1 = _re.search(r"(\d{4})年(\d{1,2})月", "".join(headers))
            if not m1:
                break
            cy, cm = int(m1.group(1)), int(m1.group(2))
            ty, tm = int(y), int(m)
            diff = (ty - cy) * 12 + (tm - cm)
            if diff == 0:
                break
            btn_sel = ".ant-picker-header-prev-btn" if diff < 0 else ".ant-picker-header-next-btn"
            try:
                f.locator(btn_sel).first.click(timeout=3000)
                time.sleep(0.8)
                flipped += 1
            except Exception:
                break
        self.sink.log(f"⚠️ 日历未找到 {date} (翻页{flipped}次)")
        return False

    def _read_selected_range(self, f):
        """读页面「已选时间」文本。兼容两种平台文案:
        单店版: '已选时间：09-14至09-14'; 品牌版: '已选时间：09-19 周六'(单日带星期)"""
        import re as _re
        try:
            txt = f.evaluate("() => document.body.innerText")
            m = _re.search(r"已选时间[：:]?\s*([\d]{2}-[\d]{2})至([\d]{2}-[\d]{2})", txt)
            if m:
                return m.group(1), m.group(2)
            # 品牌版单日: 09-19 周六 (紧跟"已选时间"才认, 防误匹配页内其它日期)
            m = _re.search(r"已选时间[：:]?\s*([\d]{2}-[\d]{2})\s*(?:周[一二三四五六日])?", txt)
            if m:
                return m.group(1), m.group(1)
        except Exception:
            pass
        return None

    def _pick_custom_range(self, f, date_from, date_to):
        """经营分析时间=「自定义」: ant-picker-range 双面板。
        交互: 打开面板时上次 range 仍激活(起点已设) → 第一次点击被当作"终点"而闭合。
        策略: 点一个"预备日期"(区间首日)闭合旧range → 重开面板(此时无激活起点) →
              依次点 起点→终点 完成 range。全程读「已选时间」验收。"""
        def mm(d):
            return d[5:]
        try:
            def open_panel():
                if self._calendar_open(f):   # 已开则不点(toggle)
                    return True
                for el in f.locator("text=自定义").all():
                    bb = el.bounding_box()
                    if bb and bb["width"] < 90 and bb["y"] < 300:
                        el.click()
                        time.sleep(1.5)
                        return True
                return False

            def read_range():
                return self._read_selected_range(f)

            cur = read_range()
            want = (mm(date_from), mm(date_to))
            self.sink.log(f"📅 当前已选: {cur}  目标: {want[0]}~{want[1]}")
            if cur == want:
                return True

            for round_ in range(3):
                open_panel()
                if not self._calendar_open(f):
                    open_panel()
                # 第1次点击: 区间首日(重置旧range)
                self._click_calendar_date(f, date_from)
                time.sleep(1)
                if not self._calendar_open(f):
                    open_panel()
                # 第2次: 起点
                self._click_calendar_date(f, date_from)
                time.sleep(0.8)
                if not self._calendar_open(f):
                    open_panel()
                # 第3次: 终点
                self._click_calendar_date(f, date_to)
                time.sleep(3)
                cur = read_range()
                self.sink.log(f"  第{round_+1}轮 → 已选: {cur}")
                if cur == want:
                    break
            if cur == want:
                self.sink.log(f"✅ 区间已生效 {cur[0]}~{cur[1]}")
                return True
            self.sink.log(f"⚠️ 区间未达目标(当前 {cur}), 数据按当前区间口径返回")
            return False
        except Exception as e:
            self.sink.log(f"⚠️ 自定义区间失败: {str(e)[:80]}")
            return False


    def _calendar_frame(self):
        """返回「打开状态」的日期弹层所在 frame。
        判据 = 真的可见的 .ant-picker-dropdown 里有日期单元格。旧写法全局数 td[title]>20,
        会被页面里别的表格误命中(改版后页面上到处是 td[title]), 必须限定在 picker 弹层内。"""
        for pg in self.ctx.pages:
            for fr in pg.frames:
                try:
                    n = fr.evaluate("""() => {
                      let cells = 0;
                      for (const dd of document.querySelectorAll('.ant-picker-dropdown')) {
                        const r = dd.getBoundingClientRect();
                        if (r.width < 50 || r.height < 50) continue;
                        cells += dd.querySelectorAll('td[title]').length;
                      }
                      return cells;
                    }""")
                    if n > 10:
                        return fr
                except Exception:
                    continue
        return None

    def _calendar_open(self, f=None):
        """日历面板真的开着 = 任一 frame 有可见的日期单元格"""
        return self._calendar_frame() is not None

    def _click_calendar_date(self, f, date, max_flip=6):
        """在 antd **区间**日历里选中 date「单日」。

        2026-09-24 真机实测:
        ① 必须用 locator 点单元格(自动换算 iframe 偏移)。用 page.mouse.click(frame 内部
           量出的坐标) 会点到顶层页面别处 —— 历史失败根因。
        ② 同一格要点 **两次**: 第一次设起点, 第二次补齐终点, 平台才显示
           「已选时间：09-21至09-21 (1日)」。
        ③ 禁用格(超出平台可选范围)直接报错, 不硬点。
        """
        flipped = 0
        for _ in range(max_flip):
            cell = self._calendar_cell(f, date)
            if cell == "disabled":
                self.sink.log(f"⚠️ {date} 在日期选择器里不可选(超出平台可选范围)")
                return False
            if cell:
                try:
                    cell.click(timeout=6000)     # 1) 起点
                    time.sleep(0.7)
                    cell.click(timeout=6000)     # 2) 终点 → 单日区间成立
                    time.sleep(0.9)
                    return True
                except Exception as e:
                    self.sink.log(f"⚠️ 点 {date} 单元格失败: {str(e)[:70]}")
                    return False
            if not self._flip_calendar(f, date):
                break
            flipped += 1
        self.sink.log(f"⚠️ 日历未找到 {date} (翻页{flipped}次)")
        return False

    def _calendar_cell(self, f, date):
        """可见的 title=date 单元格 locator; 'disabled'=该格不可选; None=当前视图没有。"""
        try:
            loc = f.locator(f'.ant-picker-dropdown td[title="{date}"]')
            n = loc.count()
        except Exception:
            return None
        for i in range(n):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                if el.evaluate("e => e.classList.contains('ant-picker-cell-disabled')"):
                    return "disabled"
                return el
            except Exception:
                continue
        return None

    def _flip_calendar(self, f, date):
        """按需翻月。面板同时显示两栏(如 2026年9月/2026年10月); 目标月不在可视区间才翻,
        在区间内却没格子 = 真没有, 返回 False 让调用方停手(防无限翻页)。"""
        import re as _re
        try:
            heads = f.evaluate("""() => [...document.querySelectorAll('.ant-picker-header-view')]
                .map(e => (e.innerText || '').trim()).filter(Boolean)""") or []
        except Exception:
            return False
        yms = []
        for h in heads:
            m = _re.search(r"(\d{4})年(\d{1,2})月", h)
            if m:
                yms.append(int(m.group(1)) * 12 + int(m.group(2)))
        if not yms:
            return False
        t = int(date[:4]) * 12 + int(date[5:7])
        if t < min(yms):
            sel = ".ant-picker-header-prev-btn"
        elif t > max(yms):
            sel = ".ant-picker-header-next-btn"
        else:
            return False
        try:
            f.locator(sel).first.click(timeout=3000)
            time.sleep(0.7)
            return True
        except Exception:
            return False


    def _parse_funnel(self, f):
        return f.evaluate("""() => {
          const t=document.body.innerText;
          const i0=t.indexOf('流量转化'), i1=t.indexOf('流量趋势');
          const m=t.slice(i0>=0?i0:0, i1>0?i1:undefined);
          const cust=(t.match(/本店\\n([^\\n]+)/)||[])[1];   // 漏斗对比列当前选的顾客维度(新客/老客)
          const ftype=(t.match(/流量类型：\\n([^\\n]+)/)||[])[1];
          const seg=(a,bb)=>{const s=m.indexOf(a),e=bb?m.indexOf(bb,s+a.length):m.length; return s>=0?m.slice(s+a.length,e>0?e:undefined):'';};
          const blocks={exp:seg('曝光','曝光\\n进店'),ent:seg('曝光\\n进店','进店\\n下单'),ord:seg('进店\\n下单',null)};
          const ppl=s=>[...s.matchAll(/([\\d,]+)\\s*人/g)].map(x=>+x[1].replace(/,/g,''));
          const rates=[...m.matchAll(/(?:进店|下单)转化率\\n([\\d.]+)%/g)].map(x=>+x[1]);
          const nums={};
          for (const [k,s] of Object.entries(blocks)) {
            const v=ppl(s);
            nums[k+'_shop']=v[0]??null; nums[k+'_top']=v[1]??null;
          }
          return {cust, bench:cust, ftype, nums, rates};
        }""")

    def _ensure_bench(self, f):
        """商圈基准: 单店版有「商圈同行前10%均值」可选; 品牌版(demo_account/示例商家)流量页**没有**。

        2026-09-30 真机核实(把整页控件逐个点开抄选项):
            流量类型 = 全部/外卖/爆品团; 统计方式 = 门店累计/门店均值; 顾客 = 新客/老客; 对比 = 比上周二/比前一日。
            没有任何「商圈同行/前10%」入口 → 本页取不到商圈TOP(模板 FA..GE/DO..EA 那些列)。
        以前这里打的是"沿用页面基准: 新客", 实际是把「顾客维度」当成了基准名 → 混淆了售后诊断。
        现在如实说明, 并把 TOP 列留空(宁可不写, 也不写错)。"""
        d = self._parse_funnel(f)
        if d.get("cust") in ("新客", "老客"):     # 本页的对比列 = 顾客维度, 不是商圈基准
            has_bench_ctl = False
        else:
            has_bench_ctl = f.evaluate("""() => {
          for (const el of document.querySelectorAll('.ant-dropdown-trigger')) {
            const r = el.getBoundingClientRect();
            if ((el.innerText || '').includes('均值') && r.width > 0) return true;
          }
          return false;
        }""")
        if not has_bench_ctl:
            self.sink.log("ℹ️ 本页无「商圈同行」基准控件(流量类型/统计方式/顾客/比上周 四类, 无商圈对比) "
                          "→ 商圈TOP列本次留空(宁可不写, 也不写错); 门店级「比商圈同行」只在页内榜单可见, "
                          "口径为同行均值非前10%, 未采信", level="warn")
            return False
        f.evaluate("""() => {
          for (const el of document.querySelectorAll('.ant-dropdown-trigger')) {
            const r=el.getBoundingClientRect();
            if ((el.innerText||'').includes('均值') && r.width>0) {
              el.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
              el.click(); return;
            }
          }
        }""")
        time.sleep(1.2)
        ok = f.evaluate("""() => {
          for (const li of document.querySelectorAll('.ant-dropdown-menu-item')) {
            const r=li.getBoundingClientRect();
            if (r.width>0 && li.innerText.trim()==='商圈同行前10%均值') { li.click(); return true; }
          }
          return false;
        }""")
        time.sleep(3)
        return ok

    def _pick_ftype(self, f, name):
        cur = self._parse_funnel(f)["ftype"] or "全部"
        if cur == name:
            return True
        f.locator("div.ant-select").filter(has_text=cur).first.click(timeout=6000)
        time.sleep(1.2)
        f.locator(f".ant-select-dropdown [title='{name}']").first.click(timeout=6000)
        time.sleep(4)
        return self._parse_funnel(f)["ftype"] == name

    def _pick_cust(self, f, name):
        """切漏斗的「顾客维度」(新客/老客/全部顾客) —— 连锁视图与单店视图都能用。

        坑(2026-10-01 真机): 旧实现靠"bounding box 320<y<400 且 width<75"猜元素 —— 那是连锁视图
        的位置规律; **单店视图**(single/business-analysis)结构不同, 直接失配 → 新客/老客读不到、
        TOP 列一直空。现在改成: 按**文本**找候选(允许带箭头后缀), 取最靠上的那个(漏斗在页面上半部,
        趋势图例在下面), 用 JS 打标记后用 locator 点(自动换算 iframe 偏移), 最后用"标签真的变了吗"验收。
        """
        try:
            cur = (self._parse_funnel(f).get("cust") or "").strip()
            if cur == name:
                return True
            marked = f.evaluate("""(nm) => {
              const want = (nm === '全部顾客') ? ['全部顾客'] : [nm];
              const all = [...document.querySelectorAll('*')].filter(el => {
                const t = (el.innerText || '').trim();
                if (!t || t.length > 8) return false;
                if (!want.some(w => t === w || t.startsWith(w))) return false;
                const r = el.getBoundingClientRect();
                return r.width > 8 && r.height > 8;
              });
              // ⚠️ 真机(2026-10-01): 顾客维度是 antd radio 按钮组, 三层嵌套
              //    DIV._customer(1152px) > DIV.ant-radio-group(240px) > LABEL(80px) > SPAN
              //    只按 y 排序会选中**最外层容器**(它和 LABEL 同 y), 点它 = 点空白 → 一直是空。
              //    正解: 去掉"还包含其它候选"的元素(只留最内层), 并优先 LABEL(真正的可点控件)。
              const inner = all.filter(el => !all.some(o => o !== el && el.contains(o)));
              const pick = inner.find(el => el.tagName === 'LABEL') || inner[0];
              document.querySelectorAll('[data-hermes-cust]').forEach(e => e.removeAttribute('data-hermes-cust'));
              if (!pick) return 0;
              pick.setAttribute('data-hermes-cust', '1');
              return inner.length;
            }""", name)
            if not marked:
                return False
            f.locator('[data-hermes-cust="1"]').first.click(timeout=5000)
            time.sleep(1.8)
            # 验收(2026-10-01 真机): 顾客维度是 antd radio 按钮组 →
            # 直接读**选中态 class** 最靠谱(比解析页面文本稳; 旧版就是卡在这一步, 明明点中了却判 False)。
            try:
                if f.evaluate("""(nm) => {
                  const hit = [...document.querySelectorAll('label.ant-radio-button-wrapper')]
                    .find(l => (l.innerText || '').trim() === nm);
                  return !!(hit && /(^|\\s)ant-radio-button-wrapper-checked(\\s|$)/.test(hit.className));
                }""", name):
                    return True
            except Exception:
                pass
            return (self._parse_funnel(f).get("cust") or "").strip() == name
        except Exception as e:
            self.sink.log(f"⚠️ 切顾客维度「{name}」失败: {str(e)[:70]}", level="warn")
            return False
