# -*- coding: utf-8 -*-
"""浏览器"后台运行"模式 单测 (2026-10-13 用户需求: 窗口默认不出现在主屏)。

需求: 自动化浏览器窗口默认藏到主屏之外, 不影响用户用电脑; 需要人工(登录/滑块/短信码)
时自动弹回主屏; 处理完自动收回。
本测试钉: ①启动参数(窗口出生即屏幕外); ②窗口控制 show/hide 的 CDP 行为与幂等;
③开关解析(环境变量>配置>平台默认Windows开); ④接线(事件钩子/各登录出口都挂了 hide)。
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

    def send(self, method, params=None):
        self.sent.append((method, params or {}))
        if method == "Browser.getWindowForTarget":
            return {"windowId": 7}
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
    BW.bind(ctx, True)
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
    ctx = _Ctx()
    BW.bind(ctx, False)
    assert BW.enabled() is False
    BW.show(_Sink())
    BW.hide()
    assert ctx.browser.cdp.sent == [], "后台关闭时不该发任何窗口指令"
    print("✅ 后台关闭: show/hide 全为空操作")


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


def main():
    test_launch_args()
    test_show_hide()
    test_disabled()
    test_resolve_default()
    test_wiring()
    print("\n✅ 浏览器后台模式 全绿")


if __name__ == "__main__":
    main()
