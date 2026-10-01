# -*- coding: utf-8 -*-
"""浏览器偏好记忆测试: 记住"这台机器上哪个浏览器能用", 下次不再白等超时

用户场景(云服务器+远程桌面): 自带 Chromium 启动超时, 系统 Chrome 正常。
若每次都从自带 Chromium 试起, 每轮任务白等 90 秒。这里把"记忆"行为钉死。
"""
import sys
import tempfile
import time
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "kernel"))

import merchant_report.browser_pref as BP

# 隔离记忆文件, 别污染真实用户数据目录
_TMP = _P(tempfile.mkdtemp()) / "browser_pref.json"
BP._path = lambda: _TMP
if _TMP.exists():
    _TMP.unlink()

import merchant_report.platforms.taobao as TB
TB._cleanup_stale_browser = lambda p: None


class FakeChromium:
    def __init__(self, behavior):
        self.behavior, self.calls = behavior, []

    def launch_persistent_context(self, profile, **kw):
        ch = kw.get("channel")
        self.calls.append(ch)
        r = self.behavior.get(ch)
        if isinstance(r, Exception):
            raise r
        return {"ctx": ch}


class FakePW:
    def __init__(self, behavior):
        self.chromium = FakeChromium(behavior)


TIMEOUT = RuntimeError("BrowserType.launch_persistent_context: Timeout 90000ms exceeded.\n"
                       "Call log:\n- <launching> ...\\ms-playwright\\chromium\\chrome.exe")
MISSING = RuntimeError("Executable doesn't exist at .../ms-playwright/chromium-1200/...")

print("=== 1. 无记忆 → 默认顺序(自带 Chromium 优先, 与 macOS 一致) ===")
assert BP.order([None, "msedge", "chrome"]) == [None, "msedge", "chrome"]
print(f"  ✓ {BP.order([None, 'msedge', 'chrome'])}")

print("=== 2. 记住 Chrome 可用后 → Chrome 排最前, 自带 Chromium 排最后 ===")
BP.remember_ok("chrome")
assert BP.preferred() == "chrome" and BP.bundled_broken() is True
o = BP.order([None, "msedge", "chrome"])
assert o[0] == "chrome" and o[-1] is None, o
print(f"  ✓ {o}（下次不再先等 Chromium 超时）")

print("=== 3. 只有自带不可用的记忆 → 系统浏览器先试, Chromium 垫底 ===")
_TMP.unlink()
BP.remember_broken()
o = BP.order([None, "msedge", "chrome"])
assert o == ["msedge", "chrome", None], o
print(f"  ✓ {o}")

print("=== 4. 环境变量/配置强制指定 → 排最前 ===")
o = BP.order([None, "msedge", "chrome"], forced="chrome")
assert o[0] == "chrome", o
o2 = BP.order([None, "msedge", "chrome"], forced="brave")
assert o2[0] == "brave", o2     # 强制值不在默认候选里也要尊重
print(f"  ✓ forced=chrome → {o[:2]}… ; forced=brave → {o2[0]}")

print("=== 5. 记忆能自愈: 自带 Chromium 又好用了 → 清掉系统浏览器偏好 ===")
_TMP.unlink()
BP.remember_ok("chrome")
assert BP.preferred() == "chrome"
BP.remember_ok(None)                       # 这次自带 Chromium 成功了
assert BP.preferred() is None and BP.bundled_broken() is False
assert BP.order([None, "msedge", "chrome"])[0] is None
print(f"  ✓ 记忆已清, 回到默认顺序")

print("=== 6. 端到端: 首次白等一次, 之后直接命中可用的浏览器 ===")
_TMP.unlink()
beh = {None: TIMEOUT, "msedge": MISSING, "chrome": "OK"}
pw = FakePW(beh)
ctx = TB.launch_browser(pw, "/tmp/prof")
assert ctx == {"ctx": "chrome"}, ctx
first = list(pw.chromium.calls)
assert first == [None, "msedge", "chrome"], first
pw2 = FakePW(beh)
TB.launch_browser(pw2, "/tmp/prof")
assert pw2.chromium.calls[0] == "chrome", f"第二次应直接用 Chrome: {pw2.chromium.calls}"
print(f"  第1次尝试 {first}（有一次超时代价）→ 第2次 {pw2.chromium.calls}（直接命中）")

print("=== 7. 自检失败记忆 + 24h 冷却(不每次白等) ===")
_TMP.unlink()
assert BP.verify_failed_recently() is False
BP.mark_verify_failed("Timeout 45000ms exceeded")
assert BP.verify_failed_recently() is True
import json as _json
_d = _json.loads(_TMP.read_text(encoding="utf-8"))
_d["verify_failed_at"] = time.time() - 25 * 3600      # 模拟 25 小时前
_TMP.write_text(_json.dumps(_d), encoding="utf-8")
assert BP.verify_failed_recently() is False, "超过 24h 应允许重验(环境可能已变好)"
print(f"  ✓ 24h 内跳过重验, 超时可重验")

print("\n✅ 浏览器偏好记忆测试全部通过")
