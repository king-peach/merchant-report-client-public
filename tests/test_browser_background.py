# -*- coding: utf-8 -*-
"""浏览器"后台运行"模式 单测 (2026-10-13 用户需求: 窗口默认不出现在主屏)。

需求: 自动化浏览器窗口默认藏到主屏之外, 不影响用户用电脑; 需要人工(登录/滑块/短信码)
时自动弹回主屏; 处理完自动收回。
本测试钉: ①启动参数(窗口出生即屏幕外); ②窗口控制 show/hide 的 CDP 行为与幂等;
③开关解析(环境变量>配置>平台默认Windows开); ④接线(事件钩子/各登录出口都挂了 hide)。

2026-10-14 扩展(需求 F「展示过程」): 手动开关的滑入/滑出动画、_manual 抑制自动收回、
动画失败降级、浏览器重开后 pending show。见文件末尾。
"""
import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report import browser_window as BW       # noqa: E402
from merchant_report import events as E                # noqa: E402
from merchant_report import orchestrator as O          # noqa: E402
from merchant_report.platforms import taobao as T      # noqa: E402
from merchant_report.platforms import meituan as M     # noqa: E402
from merchant_report.platforms import meituan_gj as G  # noqa: E402
from merchant_report.platforms import jd as J          # noqa: E402


class _Sink:
    def __init__(self):
        self.lines = []

    def log(self, msg, level="info"):
        self.lines.append((level, msg))


class _CDP:
    def __init__(self):
        self.sent = []
        self.cur = {"left": -32000, "top": -32000, "width": 1500, "height": 950}

    def send(self, method, params=None):
        self.sent.append((method, params or {}))
        if method == "Browser.getWindowForTarget":
            return {"windowId": 7}
        if method == "Browser.getWindowBounds":
            return {"bounds": dict(self.cur)}
        if method == "Browser.setWindowBounds":
            self.cur = dict((params or {}).get("bounds") or {})
            return {}
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": "t1", "type": "page"}]}
        return {}


class _Browser:
    def __init__(self):
        self.cdp = _CDP()

    def new_browser_cdp_session(self):
        return self.cdp


class _Page:
    def __init__(self):
        self.front = 0

    def bring_to_front(self):
        self.front += 1


class _Ctx:
    def __init__(self):
        self.browser = _Browser()
        self.pages = [_Page()]


def _bounds(sent):
    last = None
    for m, p in sent:
        if m == "Browser.setWindowBounds":
            last = p
    return last


def _moves(sent):
    return [p["bounds"] for m, p in sent if m == "Browser.setWindowBounds"]


def _reset(ctx=None, background=True):
    BW._pending = None
    BW._manual = None
    BW._warned = False
    BW.bind(ctx or _Ctx(), background)


def test_launch_args():
    a = T._browser_launch_args(True)
    assert "--window-position=-32000,-32000" in a, a
    assert "--start-maximized" not in a, a
    assert "--disable-blink-features=AutomationControlled" in a
    b = T._browser_launch_args(False)
    assert "--start-maximized" in b and not any("window-position" in x for x in b), b
    print("✅ 启动参数: 后台=屏幕外出生 / 前台=最大化")


def test_show_hide():
    ctx = _Ctx()
    _reset(ctx, True)
    assert BW.enabled() is True
    s = _Sink()
    BW.show(s, reason="登录/滑块验证")
    bp = _bounds(ctx.browser.cdp.sent)
    assert bp and bp["bounds"]["left"] == 80 and bp["bounds"]["windowState"] == "normal", bp
    assert ctx.pages[0].front == 1, "应把窗口置顶"
    assert any("弹回主屏" in m for _l, m in s.lines), s.lines
    BW.hide(s)
    bp2 = _bounds(ctx.browser.cdp.sent)
    assert bp2 and bp2["bounds"]["left"] == -32000, bp2
    n2 = len(ctx.browser.cdp.sent)
    BW.hide(s)   # 幂等: 未显示时不再发指令
    assert len(ctx.browser.cdp.sent) == n2, "重复 hide 不应再发 CDP"
    print("✅ show/hide: 弹回主屏→收回屏幕外; 幂等")


def test_disabled():
    """后台**自动**弹/收关掉时不开火; 但「展示过程」手动开关仍可用(enabled 只认 ctx)。"""
    ctx = _Ctx()
    _reset(ctx, False)
    assert BW.enabled() is True, "窗口控制可用性只看是否绑定了浏览器(手动开关不依赖后台模式)"
    BW.show(_Sink())          # 自动弹: 后台模式关 → 不动窗口
    BW.hide()
    assert ctx.browser.cdp.sent == [], "后台关闭时自动 show/hide 不该发任何窗口指令"
    # 手动开关不受后台模式影响(需求 F): 用户点了就要能看到/收回浏览器
    BW.request(False)
    BW.poll(_Sink())
    assert _moves(ctx.browser.cdp.sent)[-1]["left"] == -32000, "手动收回应滑出屏外"
    BW.request(True)
    BW.poll(_Sink())
    assert _moves(ctx.browser.cdp.sent)[-1]["left"] == 80, "手动展示应滑入主屏"
    print("✅ 后台关闭: 自动 show/hide 空操作; 手动「展示过程」仍生效")


def test_resolve_default():
    assert BW.resolve_default(None, is_windows=True) is True
    assert BW.resolve_default(None, is_windows=False) is False
    assert BW.resolve_default(False, is_windows=True) is False
    assert BW.resolve_default(True, is_windows=False) is True
    old = os.environ.get("MRC_BROWSER_BACKGROUND")
    try:
        os.environ["MRC_BROWSER_BACKGROUND"] = "0"
        assert BW.resolve_default(True, is_windows=True) is False, "环境变量优先"
        os.environ["MRC_BROWSER_BACKGROUND"] = "1"
        assert BW.resolve_default(False, is_windows=False) is True, "环境变量优先"
    finally:
        if old is None:
            os.environ.pop("MRC_BROWSER_BACKGROUND", None)
        else:
            os.environ["MRC_BROWSER_BACKGROUND"] = old
    print("✅ 开关解析: 环境变量 > 配置 > 平台默认(Windows开)")


def test_wiring():
    nh = inspect.getsource(E.EventSink.need_human)
    assert '"login", "sms_code"' in nh and "browser_window" in nh, "登录/短信事件要自动弹回主屏"
    assert "browser_hide" in inspect.getsource(E.EventSink), "要提供 browser_hide 便捷口"
    assert "browser_window" in inspect.getsource(E.EventSink.result), "任务收尾要兜底收回"
    assert "browser_hide" in inspect.getsource(T.TaobaoAdapter.ensure_login)
    assert "browser_hide" in inspect.getsource(M.MeituanAdapter.ensure_login)
    assert "browser_hide" in inspect.getsource(G.MeituanGjAdapter._wait_manual)
    assert "browser_hide" in inspect.getsource(J.JDAdapter.ensure_login)
    lb = inspect.getsource(T.launch_browser)
    assert "_browser_launch_args(background)" in lb and "_bw.bind(ctx, background)" in lb
    rt = inspect.getsource(O.run_task)
    assert "background=_bg_pref" in rt and "sink.browser_hide()" in rt
    print("✅ 接线: 事件钩子 + 四处登录出口 + 启动参数 + orchestrator 全在位")


# ---------- 需求 F「展示过程」(2026-10-14) ----------

def test_manual_show_animates():
    """请求开 → 多帧动画 + 终点坐标正确 + _visible=True。"""
    ctx = _Ctx()
    _reset(ctx, True)
    s = _Sink()
    BW.request(True)                     # 读取线程语义: 只改标志
    assert BW._visible is False
    BW.poll(s)                           # 主线程安全点: 才真正动窗口
    mv = _moves(ctx.browser.cdp.sent)
    assert len(mv) > 1, f"应当是逐帧滑入动画, 实际只发了 {len(mv)} 次 setWindowBounds"
    assert mv[-1]["left"] == 80 and mv[-1]["top"] == 80, mv[-1]
    assert mv[-1].get("width") == 1500 and mv[-1].get("height") == 950, mv[-1]
    assert BW._visible is True
    assert ctx.pages[0].front >= 1, "滑入后要置前"
    assert any("弹回主屏" in m for _l, m in s.lines), s.lines
    print(f"✅ 手动展示: {len(mv)} 帧滑入 → (80,80,1500,950), 已置前")


def test_manual_hide_animates():
    """请求关 → 终点含 -32000, 且 _visible=False。"""
    ctx = _Ctx()
    _reset(ctx, True)
    s = _Sink()
    BW.request(True); BW.poll(s)
    n0 = len(_moves(ctx.browser.cdp.sent))
    BW.request(False); BW.poll(s)
    mv = _moves(ctx.browser.cdp.sent)[n0:]
    assert len(mv) > 0
    assert mv[-1]["left"] == -32000 and mv[-1]["top"] == -32000, mv[-1]
    assert BW._visible is False
    assert any("收回屏外" in m for _l, m in s.lines), s.lines
    print(f"✅ 手动收回: {len(mv)} 帧滑出 → (-32000,-32000)")


def test_manual_suppresses_auto_hide():
    """_manual=True 时自动 hide() 不产生任何移动(用户正在看)。"""
    ctx = _Ctx()
    _reset(ctx, True)
    BW.request(True); BW.poll(_Sink())          # 用户手动展示中
    n = len(ctx.browser.cdp.sent)
    BW.hide(_Sink())                            # 人工环节结束的自动收回
    BW.hide(_Sink())                            # result() 兜底
    assert len(ctx.browser.cdp.sent) == n, "用户手动开着展示时, 自动 hide 必须让路"
    assert BW._visible is True
    print("✅ 手动展示中: 自动 hide 让路(不移动窗口)")


def test_anim_failure_falls_back():
    """动画中途 CDP 抛错 → 至少一次 setBounds 落到目标坐标(降级路径)。"""
    class _Flaky(_CDP):
        def __init__(self):
            super().__init__()
            self.raised = 0

        def send(self, method, params=None):
            if method == "Browser.setWindowBounds" and self.raised < 3:
                self.raised += 1
                self.sent.append((method, params or {}))
                raise RuntimeError("CDP 掉线(模拟动画中途失败)")
            return super().send(method, params)

    ctx = _Ctx()
    ctx.browser.cdp = _Flaky()
    _reset(ctx, True)
    BW.request(True)
    BW.poll(_Sink())
    mv = _moves(ctx.browser.cdp.sent)
    assert any(b["left"] == 80 and b["top"] == 80 for b in mv), \
        f"动画失败必须退化为一次性跳到位, 实际: {mv[-3:]}"
    assert BW._visible is True
    print("✅ 动画失败: 降级为一次性 setBounds 到目标")


def test_anim_failure_silent_warn():
    """窗口控制彻底不可用: 不抛异常 + 只警告一次(绝不因为窗口失败影响任务)。"""
    class _Dead(_CDP):
        def send(self, method, params=None):
            raise RuntimeError("窗口控制完全不可用")

    ctx = _Ctx()
    ctx.browser.cdp = _Dead()
    _reset(ctx, True)
    s = _Sink()
    BW.request(True)
    BW.poll(s)                    # 绝不抛
    BW.hide(s)
    warn = [m for lv, m in s.lines if lv == "warn"]
    assert len(warn) <= 1, f"警告只许一次(别刷屏): {s.lines}"
    print("✅ 窗口控制不可用: 静默降级 + 最多一次警告")


def test_rebind_pending_show():
    """bind 时用户还开着「展示过程」→ 记一次 pending show(浏览器重开后自动回到展示态)。"""
    ctx = _Ctx()
    _reset(ctx, True)
    BW.request(True)
    BW.bind(_Ctx(), True)               # 模拟浏览器重开
    assert BW._pending == "show", "重开浏览器后要记住用户还开着展示"
    assert BW._visible is False, "刚重开的浏览器出生在屏外, 等安全点再滑入"
    BW.poll(_Sink())
    assert BW._visible is True
    print("✅ bind 重登记: _manual=True → pending show → 下一安全点滑回主屏")


def test_non_owner_thread_defers():
    """CDP 只许主线程: 别的线程 poll() 不执行(只留标志), 由主线程安全点接手。"""
    import threading
    ctx = _Ctx()
    _reset(ctx, True)
    BW.request(True)
    threading.Thread(target=lambda: BW.poll(_Sink()), daemon=True).start()
    import time as _t
    _t.sleep(0.2)
    assert not any(m == "Browser.setWindowBounds" for m, _ in ctx.browser.cdp.sent), \
        "非主线程绝不允许发 CDP(Playwright sync API 绑定主线程)"
    assert BW._pending == "show", "标志要留着等主线程"
    BW.poll(_Sink())
    assert BW._visible is True
    print("✅ 线程纪律: 非主线程 poll 只留标志, 主线程执行")


def test_safety_hooks_wired():
    """安全点接线: EventSink.log/step 带 poll; 主要等待循环里有显式 poll。"""
    assert "_poll_window" in inspect.getsource(E.EventSink.log)
    assert "_poll_window" in inspect.getsource(E.EventSink.step)
    assert "_poll_window()" in inspect.getsource(T.TaobaoAdapter.ensure_login), "淘宝登录等待循环"
    assert "_poll_window()" in inspect.getsource(M.MeituanAdapter.ensure_login), "美团登录等待循环"
    assert "_poll_window()" in inspect.getsource(M.MeituanAdapter._report_page), "美团报表页就绪等待"
    assert "_poll_window()" in inspect.getsource(J.JDAdapter.ensure_login), "京东登录等待循环"
    assert "_poll_window()" in inspect.getsource(J.JDAdapter.download_reports), "京东下载列表轮询"
    assert "_poll_window()" in inspect.getsource(G.MeituanGjAdapter._wait_manual), "管家登录等待循环"
    # 启动参数 + 开场滑入
    assert "--show-browser" in inspect.getsource(__import__(
        "merchant_report.task_main", fromlist=["main"]).main)
    rt = inspect.getsource(O.run_task)
    assert "show_browser" in rt and "request(True)" in rt, "orchestrator 开场滑入"
    print("✅ 安全点: log/step 兜底 + 5 处等待循环 + 开场滑入 + --show-browser")


def main():
    test_launch_args()
    test_show_hide()
    test_disabled()
    test_resolve_default()
    test_wiring()
    test_manual_show_animates()
    test_manual_hide_animates()
    test_manual_suppresses_auto_hide()
    test_anim_failure_falls_back()
    test_anim_failure_silent_warn()
    test_rebind_pending_show()
    test_non_owner_thread_defers()
    test_safety_hooks_wired()
    print("\n✅ 浏览器后台模式 + 「展示过程」 全绿")


if __name__ == "__main__":
    main()
