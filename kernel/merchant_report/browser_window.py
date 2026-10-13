# -*- coding: utf-8 -*-
"""自动化浏览器"后台运行"窗口控制 (2026-10-13 用户需求)。

需求: 默认把自动化浏览器窗口**藏到主屏之外**(不影响用户正常用电脑);
      需要人工操作(登录/滑块/短信验证码)时自动弹回主屏; 处理完自动收回后台。

为什么不用无头/最小化:
  · headless(无头): 淘宝/美团风控识别凶狠, 滑块大概率过不去(历史教训);
  · 最小化: Chromium 会把窗口当"后台页面"(渲染节流/visibilityState 变化),
    个别页面行为可能变 —— 还多一个"任务在跑但页面像死了"的坑;
  · 屏幕外放置: 仍是**有头**浏览器(指纹不变)、页面一切照常, 只是人看不见。

开关: defaults.browser_background(布尔) > 环境变量 MRC_BROWSER_BACKGROUND=0/1(调试) >
      平台默认(Windows=开, 其他=关)。launch 时由 orchestrator 读配置传入。
窗口控制失败一律静默降级(绝不因为藏窗口失败而影响任务)。
"""
import os

HIDE_LEFT, HIDE_TOP = -32000, -32000          # 屏幕外坐标(Windows/mac 通用惯例)
SHOW_LEFT, SHOW_TOP, SHOW_W, SHOW_H = 80, 80, 1500, 950

_ctx = None
_background = False
_shown = False                                 # 当前是否因为人工操作而临时显示


def resolve_default(cfg_default=None, is_windows=None):
    """配置 > 环境变量 > 平台默认。cfg_default=None 表示"未配置"。返回 bool。"""
    env = os.environ.get("MRC_BROWSER_BACKGROUND", "")
    if str(env).strip() != "":
        return str(env).strip().lower() not in ("0", "false", "no", "off")
    if cfg_default is not None:
        return bool(cfg_default)
    if is_windows is None:
        is_windows = (os.name == "nt")
    return bool(is_windows)


def bind(ctx, background):
    """launch 成功后登记: ctx + 是否后台模式(重开浏览器会再次调用, 状态重置)。"""
    global _ctx, _background, _shown
    _ctx, _background, _shown = ctx, bool(background), False


def enabled():
    return bool(_background and _ctx is not None)


def _cdp():
    if _ctx is None:
        return None
    try:
        return _ctx.browser.new_browser_cdp_session()
    except Exception:
        return None


def _window_id(cdp):
    try:
        wid = cdp.send("Browser.getWindowForTarget").get("windowId")
        if wid is not None:
            return wid
    except Exception:
        pass
    try:
        tid = ""
        for t in (cdp.send("Target.getTargets").get("targetInfos") or []):
            if t.get("type") == "page" and t.get("targetId"):
                tid = t["targetId"]
                break
        if tid:
            return cdp.send("Browser.getWindowForTarget", {"targetId": tid}).get("windowId")
    except Exception:
        pass
    return None


def _set_bounds(left, top, width=None, height=None):
    try:
        cdp = _cdp()
        if cdp is None:
            return False
        wid = _window_id(cdp)
        if wid is None:
            return False
        b = {"left": int(left), "top": int(top), "windowState": "normal"}
        if width:
            b["width"] = int(width)
        if height:
            b["height"] = int(height)
        cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": b})
        return True
    except Exception:
        return False


def show(sink=None, reason=""):
    """把窗口弹回主屏并置顶(登录/滑块/短信验证码等人工点触发)。"""
    global _shown
    if not enabled():
        return
    _shown = True
    ok = _set_bounds(SHOW_LEFT, SHOW_TOP, SHOW_W, SHOW_H)
    try:
        if _ctx is not None and _ctx.pages:
            _ctx.pages[0].bring_to_front()
    except Exception:
        pass
    try:
        if sink is not None:
            if ok:
                sink.log("🖥 自动化浏览器已弹回主屏（需要你操作%s；处理完会自动收回后台）"
                         % (("：" + str(reason)) if reason else ""))
            else:
                sink.log("⚠️ 浏览器窗口未能自动弹回主屏（窗口控制不可用）—— 若看不到窗口, "
                         "请在任务栏找一下浏览器", level="warn")
    except Exception:
        pass


def hide(sink=None):
    """把窗口收回屏幕外。多次调用安全(未显示时不动)。任务各出口都调用它兜底。"""
    global _shown
    if not enabled():
        _shown = False
        return
    if not _shown:
        return
    _shown = False
    _set_bounds(HIDE_LEFT, HIDE_TOP)
