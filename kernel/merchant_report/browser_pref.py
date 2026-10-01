# -*- coding: utf-8 -*-
"""浏览器偏好记忆: 记住"这台机器上哪个浏览器能用", 下次直接用, 不再白等超时。

背景(用户 VPS 实测, 2026-09-22): 云服务器+远程桌面环境里 Playwright 自带的 Chromium
起不来(启动超时), 而系统装的 Chrome 正常。默认顺序 [自带Chromium → msedge → chrome]
意味着每次任务都要先白等 90 秒才知道要用 Chrome。

于是:
- 启动失败/降级成功时记下"自带 Chromium 不可用"
- 某个候选启动成功时记下它作为首选
- 下次启动直接按记忆的顺序来(首选在最前, 自带 Chromium 若已知不可用则排到最后)
记忆文件: <app_data>/browser_pref.json —— 用户可删掉以恢复默认顺序。
"""
import json
import time

from .config import app_data_dir

CHANNELS = ("chrome", "msedge")          # 系统浏览器候选(None = 自带 Chromium)


def _path():
    return app_data_dir() / "browser_pref.json"


def load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict):
    try:
        _path().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def preferred():
    """记住的首选 channel(字符串) 或 None(无偏好)。"""
    v = load().get("preferred")
    return v if v in CHANNELS else None


def bundled_broken() -> bool:
    """自带 Chromium 是否已被判过不可用(自检失败/启动失败)。"""
    return bool(load().get("bundled_broken"))


def remember_ok(channel):
    """记下能用的候选。channel=None 表示自带 Chromium 可用(清掉系统浏览器偏好)。"""
    d = load()
    if channel in CHANNELS:
        d["preferred"], d["bundled_broken"] = channel, True   # 用系统浏览器 → 自带那个也不可用
        d["note"] = f"上次成功用 {channel}"
    else:
        d["preferred"], d["bundled_broken"] = None, False
        d["note"] = "上次成功用自带 Chromium"
    d["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(d)


def remember_broken():
    """自带 Chromium 不可用(自检失败/启动超时) → 下次不再第一个试它。"""
    d = load()
    d["bundled_broken"] = True
    d.setdefault("note", "自带 Chromium 启动失败")
    d["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(d)


def mark_verify_failed(reason: str = ""):
    """记下自检失败(+时间): 24 小时内不再重复自检, 免得每次任务白等超时。"""
    d = load()
    d["verify_failed_at"] = time.time()
    d["verify_failed_reason"] = str(reason)[:200]
    _save(d)


def verify_failed_recently(hours: int = 24) -> bool:
    """24 小时内自检失败过 → 直接跳过自检; 超过则允许重验(环境可能已变好)。"""
    at = load().get("verify_failed_at")
    try:
        return bool(at) and (time.time() - float(at)) < hours * 3600
    except Exception:
        return False


def note() -> str:
    """给日志用的一句话说明当前记忆状态。"""
    d = load()
    if d.get("preferred"):
        return f"记得上次用 {d['preferred']}（自带 Chromium 不可用）"
    if d.get("bundled_broken"):
        return "记得自带 Chromium 不可用"
    return ""


def order(default_order, forced=None):
    """按记忆排出尝试顺序。default_order 形如 [None, "msedge", "chrome"]。
    forced: 配置/环境变量强制的 channel(字符串), 有则排最前。"""
    cand = list(default_order)
    if bundled_broken() and None in cand:
        cand.remove(None)
        cand.append(None)                 # 已知不可用 → 排到最后
    pref = forced or preferred()
    if pref and pref in cand:
        cand.remove(pref)
        cand.insert(0, pref)
    elif forced and forced not in cand:
        cand.insert(0, forced)
    return cand
