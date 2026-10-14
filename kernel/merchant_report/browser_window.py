# -*- coding: utf-8 -*-
"""自动化浏览器窗口控制: "后台运行"(2026-10-13) + 「展示过程」开关(2026-10-14)。

两条需求:
 A. **后台模式**(平台默认: Windows 开): 窗口藏到**主屏之外**, 不打扰用户用电脑;
    需要人工(登录/滑块/短信码)时自动弹回主屏, 处理完自动收回。
    自动弹/收**只在后台模式生效**(行为与 2026-10-13 完全一致)。
 B. **「展示过程」手动开关**(前端面板, 默认关): 运行中随时可点 —— 点开把自动化浏览器
    **滑入主屏**(看一眼它到底在干嘛), 再点**滑出屏外**, 任务照常奔跑(不中断、不影响取数);
    开着的话下一轮任务开场就滑入(前端记忆开关值, 启动时把开关带下来)。

铁律(踩过才知道的):
  · Playwright sync API 绑**主线程**, CDP 只许主线程碰 → stdin 读取线程**只许改标志**
    (见 request()); 真正的窗口操作一律放到**主线程安全点** poll() 里执行。
  · 窗口控制失败**一律静默降级**(绝不因为藏/弹窗口失败而影响任务), 只在彻底失败时警告一次。

为什么不用无头/最小化:
  · headless(无头): 淘宝/美团风控识别凶狠, 滑块大概率过不去(历史教训);
  · 最小化: Chromium 会把窗口当"后台页面"(渲染节流/visibilityState 变化), 个别页面行为可能变;
  · 屏幕外放置: 仍是**有头**浏览器(指纹不变)、页面一切照常, 只是人看不见。

开关解析: defaults.browser_background(布尔) > 环境变量 MRC_BROWSER_BACKGROUND=0/1(调试) >
          平台默认(Windows=开, 其他=关)。launch 时由 orchestrator 读配置传入。
"""
import os
import threading
import time

HIDE_LEFT, HIDE_TOP = -32000, -32000          # 屏幕外坐标(Windows/mac 通用惯例)
SHOW_LEFT, SHOW_TOP, SHOW_W, SHOW_H = 80, 80, 1500, 950
EDGE_MARGIN = 80                               # 滑入/滑出动画起点: 屏幕左缘外这么多像素

_ctx = None
_background = False          # 后台模式(自动弹/收是否生效)
_visible = False             # 当前窗口是否在主屏(逻辑状态; 每步以最新动作为准)
_manual = None               # 用户手动开关: None=没点过 / True=展示过程 / False=收回屏外
_pending = None              # 待执行的窗口动作: None/"show"/"hide"(最新者胜), 只由 poll() 消费
_owner_ident = None          # 主线程 id(CDP 只许它碰)
_cur_left, _cur_top = HIDE_LEFT, HIDE_TOP     # 最近一次已知位置(CDP 读不到时的兜底)
_warned = False              # "窗口控制失败"只提示一次(不刷屏)
_polling = False             # poll() 重入保护(动画里可能经 sink.log 再进来)
_default_sink = None         # 事件出口(EventSink 注册): 等待循环里 poll() 没带 sink 时用它记日志


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
    """launch 成功后登记: ctx + 是否后台模式(重开浏览器会再次调用, 位置状态重置)。

    手动开关(_manual)是**跨浏览器实例**的用户意图, 不重置 —— 重开后若用户还开着
    「展示过程」, 记一次 pending show, 下一轮安全点自动滑回主屏(不用再点一次)。
    """
    global _ctx, _background, _visible, _pending, _owner_ident, _cur_left, _cur_top
    _ctx = ctx
    _background = bool(background)
    _owner_ident = threading.get_ident()
    _visible = not _background
    _pending = "show" if _manual is True else None
    _cur_left, _cur_top = (SHOW_LEFT, SHOW_TOP) if _visible else (HIDE_LEFT, HIDE_TOP)


def enabled():
    """窗口控制是否可用(= 已绑定浏览器)。手动开关不依赖后台模式。"""
    return _ctx is not None


def set_sink(sink):
    """登记事件出口(EventSink 构造时调用一次): 等待循环里的 poll() 不带 sink 也能把
    「已弹回主屏 / 已收回屏外」这类反馈给到用户。"""
    global _default_sink
    _default_sink = sink


def _auto_enabled():
    """**自动**弹/收是否生效: 只在后台模式(行为与 2026-10-13 一致)。"""
    return _ctx is not None and _background


def request(visible, sink=None):
    """「展示过程」开关: **线程安全**, 只改标志, **绝不碰 CDP**(stdin 读取线程会调这里)。

    真正的窗口操作留给主线程安全点 poll() —— Playwright sync API 绑主线程, 从别的线程发
    CDP 会直接把任务搞崩。sink 参数保留是为了将来记录调试信息(当前不用)。
    """
    global _manual, _pending
    _manual = bool(visible)
    _pending = "show" if visible else "hide"


def poll(sink=None):
    """主线程**安全点**: 执行待处理的窗口动作(滑入/滑出动画)。

    调用点: EventSink.log()/step()(全链路最密集的公共调用) + 各主要等待循环。
    未启用/不在主线程/没有待办 → **零成本 no-op**(一次属性读取即返回)。
    """
    global _pending, _visible, _polling
    if _pending is None:
        return
    if _ctx is None or _polling:
        return
    if _owner_ident is not None and threading.get_ident() != _owner_ident:
        return                                   # 非主线程: 只留标志, 等主线程来执行
    if sink is None:
        sink = _default_sink
    _polling = True
    try:
        act, _pending = _pending, None
        if act == "show":
            if _visible:
                _front()
                _log(sink, "👁 展示过程：浏览器已在主屏（已置前）")
                return
            ok = _animate(SHOW_LEFT, SHOW_TOP, SHOW_W, SHOW_H, sink=sink)
            _visible = True
            _front()
            if ok:
                _log(sink, "👁 展示过程：已弹回主屏（点『展示过程』可随时收回）")
            else:
                _warn_once(sink, "⚠️ 展示过程：窗口未能滑入主屏（窗口控制不可用），任务照常继续")
        else:
            if not _visible:
                return
            ok = _animate(HIDE_LEFT, HIDE_TOP, sink=sink)
            _visible = False
            if ok:
                _log(sink, "🫥 展示过程：已收回屏外，任务继续奔跑")
    finally:
        _polling = False


def poll_safe(sink=None):
    """poll() 的"绝不抛"版本 —— 给平台**等待循环**里直接调用(窗口控制失败绝不影响任务)。"""
    try:
        poll(sink)
    except Exception:
        pass


def _cdp():
    if _ctx is None:
        return None
    try:
        return _ctx.browser.new_browser_cdp_session()
    except Exception:
        return None


def _front():
    try:
        if _ctx is not None and _ctx.pages:
            _ctx.pages[0].bring_to_front()
    except Exception:
        pass


def _log(sink, msg, level="info"):
    try:
        if sink is not None:
            sink.log(msg, level=level)
    except Exception:
        pass


def _warn_once(sink, msg, level="warn"):
    """窗口控制失败的提示**只给一次**(其余静默): 失败绝不影响任务, 但也别让用户一头雾水。"""
    global _warned
    if _warned:
        return
    _warned = True
    _log(sink, msg, level=level)


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


def _send_bounds(cdp, wid, left, top, width=None, height=None):
    b = {"left": int(left), "top": int(top), "windowState": "normal"}
    if width:
        b["width"] = int(width)
    if height:
        b["height"] = int(height)
    cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": b})


def _read_bounds(cdp, wid):
    """当前窗口位置(读不到返回 None —— 用模块内记录的位置兜底)。"""
    try:
        b = (cdp.send("Browser.getWindowBounds", {"windowId": wid}) or {}).get("bounds") or {}
        if "left" in b and "top" in b:
            return (int(b["left"]), int(b["top"]),
                    int(b["width"]) if b.get("width") else None,
                    int(b["height"]) if b.get("height") else None)
    except Exception:
        pass
    return None


def _walk(cdp, wid, x0, y0, x1, y1, w, h, steps, dur=0.3):
    """按帧插值移动(**复用同一个 CDP session**; 每帧新建 session 又慢又容易失败)。"""
    pause = max(0.0, float(dur) / max(1, steps))
    for i in range(1, steps + 1):
        x = x1 if i == steps else x0 + (x1 - x0) * i / steps
        y = y1 if i == steps else y0 + (y1 - y0) * i / steps
        _send_bounds(cdp, wid, round(x), round(y), w, h)
        if i < steps and pause:
            time.sleep(pause)


def _set_bounds(left, top, width=None, height=None):
    try:
        cdp = _cdp()
        if cdp is None:
            return False
        wid = _window_id(cdp)
        if wid is None:
            return False
        _send_bounds(cdp, wid, left, top, width, height)
        return True
    except Exception:
        return False


def _animate(target_l, target_t, width=None, height=None, steps=None, sink=None):
    """滑入/滑出动画(必须主线程)。成功 True; 失败 → 一次性 setBounds 到目标(降级)。

    入场(当前在屏幕外): 先**瞬时**到屏幕左缘外(看不到"从角落飞过来"的怪画面), 再滑到目标;
    出场: 反向 —— 先滑到屏幕左缘外, 再瞬移回屏幕外省掉长距离插值。
    """
    global _cur_left, _cur_top
    cdp = _cdp()
    wid = _window_id(cdp) if cdp is not None else None
    if wid is None:
        return _fallback_apply(target_l, target_t, width, height, sink)
    try:
        got = _read_bounds(cdp, wid)
        cur_l, cur_t, cur_w, cur_h = got if got else (_cur_left, _cur_top, None, None)
        w = int(width or cur_w or SHOW_W)
        h = int(height or cur_h or SHOW_H)
        n = int(steps) if steps else (9 + int(time.time() * 1000) % 5)      # 9~13 帧
        n = max(2, n)
        if target_l <= HIDE_LEFT:                       # ── 出场 ──
            edge = -(w + EDGE_MARGIN)
            if cur_l > HIDE_LEFT + 4:
                _walk(cdp, wid, cur_l, cur_t, edge, target_t, w, h, n)
            _send_bounds(cdp, wid, target_l, target_t, w, h)   # 瞬移回屏幕外
        else:                                           # ── 入场 ──
            if cur_l <= HIDE_LEFT + 4:
                _send_bounds(cdp, wid, -(w + EDGE_MARGIN), target_t, w, h)
                cur_l, cur_t = -(w + EDGE_MARGIN), target_t
            _walk(cdp, wid, cur_l, cur_t, target_l, target_t, w, h, n)
        _cur_left, _cur_top = int(target_l), int(target_t)
        return True
    except Exception:
        return _fallback_apply(target_l, target_t, width, height, sink)


def _fallback_apply(target_l, target_t, width, height, sink):
    """动画任何一步失败 → 退化为一次性跳到位(动画只是观感, 到位才要紧)。"""
    global _cur_left, _cur_top
    if _set_bounds(target_l, target_t, width, height):
        _cur_left, _cur_top = int(target_l), int(target_t)
        return True
    _warn_once(sink, "⚠️ 浏览器窗口控制失败（窗口未能移动）：不影响任务，继续跑")
    return False


def show(sink=None, reason=""):
    """**自动**弹回主屏并置顶(登录/滑块/短信验证码等人工点触发)。仅后台模式生效。"""
    global _visible
    if not _auto_enabled():
        return
    if _visible or _manual is True:
        _front()          # 用户正看着(或手动开着展示) → 置前即可, 不必再动坐标
        return
    ok = _animate(SHOW_LEFT, SHOW_TOP, SHOW_W, SHOW_H, sink=sink)
    _visible = True
    _front()
    if ok:
        _log(sink, "🖥 自动化浏览器已弹回主屏（需要你操作%s；处理完会自动收回后台）"
             % (("：" + str(reason)) if reason else ""))
    else:
        _warn_once(sink, "⚠️ 浏览器窗口未能自动弹回主屏（窗口控制不可用）—— 若看不到窗口, "
                         "请在任务栏找一下浏览器")


def hide(sink=None):
    """**自动**收回屏幕外(人工环节结束 + result() 兜底)。幂等; 仅后台模式生效。

    用户手动开着「展示过程」时**跳过**(用户正在看, 不许自动收回)。
    """
    global _visible
    if not _auto_enabled():
        return
    if _manual is True:
        return
    if not _visible:
        return
    _visible = False
    _animate(HIDE_LEFT, HIDE_TOP, sink=sink)
