# -*- coding: utf-8 -*-
"""浏览器启动回退测试: 启动超时必须换候选(不能直接失败), 且错误信息要带可操作建议

用户 Windows 实测: 'BrowserType.launch_persistent_context: Timeout 180000ms exceeded'
—— 自带 Chromium 下载成功但启动超时。旧代码的回退关键字里没有 timeout, 于是直接抛错失败,
而不是回退系统 Edge。这里把该行为钉死。
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "kernel"))

import merchant_report.platforms.taobao as TB
import merchant_report.browser_pref as BP

# 隔离浏览器偏好记忆文件(否则测试会写进真实用户目录, 并让用例间互相影响)
import tempfile as _tf
from pathlib import Path as _PP
_BP = _PP(_tf.mkdtemp()) / "browser_pref.json"
BP._path = lambda: _BP

TB._cleanup_stale_browser = lambda p: None      # 不碰进程表


class FakeChromium:
    """按 channel 决定行为: behavior[channel] 为 Exception 则抛, 否则返回标记对象"""

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def launch_persistent_context(self, profile, **kw):
        ch = kw.get("channel")
        self.calls.append({"channel": ch, "timeout": kw.get("timeout")})
        r = self.behavior.get(ch)
        if isinstance(r, Exception):
            raise r
        return {"ctx": ch, "profile": profile}


class FakePW:
    def __init__(self, behavior):
        self.chromium = FakeChromium(behavior)


TIMEOUT_ERR = RuntimeError(
    "BrowserType.launch_persistent_context: Timeout 90000ms exceeded.\nCall log:\n"
    "- <launching> C:\\Users\\admin\\AppData\\Roaming\\MerchantReportClient\\ms-playwright\\"
    "chromium-1200\\chrome-win\\chrome.exe --disable-blink-features=AutomationControlled\n"
    "- <launching>   userDataDir=\"...\"\n- <launching> <process did not exit>")

MISSING_ERR = RuntimeError(
    "BrowserType.launch_persistent_context: Executable doesn't exist at "
    "/Users/x/Library/Caches/ms-playwright/chromium-1200/chrome-mac/Chromium.app/...")

if _BP.exists():
    _BP.unlink()
print("=== 1. 自带 Chromium 启动超时 → 自动回退系统 Edge(不再直接失败) ===")
pw = FakePW({None: TIMEOUT_ERR, "msedge": "OK_EDGE", "chrome": MISSING_ERR})
ctx = TB.launch_browser(pw, "/tmp/prof")
assert ctx == {"ctx": "msedge", "profile": "/tmp/prof"}, ctx
tried = [c["channel"] for c in pw.chromium.calls]
assert tried == [None, "msedge"], tried
assert all(c["timeout"] == TB.LAUNCH_TIMEOUT_MS for c in pw.chromium.calls), pw.chromium.calls
print(f"  ✓ 尝试顺序 {tried}, 均带 {TB.LAUNCH_TIMEOUT_MS}ms 超时(默认 180s 太长)")

if _BP.exists():
    _BP.unlink()
print("=== 2. 自带 Chromium 不存在 → 回退 Edge(原有行为保持) ===")
pw = FakePW({None: MISSING_ERR, "msedge": "OK_EDGE"})
ctx = TB.launch_browser(pw, "/tmp/prof")
assert ctx["ctx"] == "msedge"
print(f"  ✓ 回退成功")

if _BP.exists():
    _BP.unlink()
print("=== 3. 全部候选都失败 → 报错含可操作建议 + 原始 Call log 片段 ===")
pw = FakePW({None: TIMEOUT_ERR, "msedge": TIMEOUT_ERR, "chrome": TIMEOUT_ERR})
try:
    TB.launch_browser(pw, "/tmp/prof")
    raise AssertionError("应当抛错")
except RuntimeError as e:
    msg = str(e)
    assert "已尝试" in msg, msg
    assert "白名单" in msg, "缺少安全软件白名单建议(Windows 上最常见原因)"
    assert "Edge" in msg and "Chrome" in msg, msg
    assert "ms-playwright" in msg or "Call log" in msg, "应保留原始信息便于定位"
    assert len(msg) > 400, "错误信息太短, 丢了关键现场"
    print(f"  ✓ 报错长度 {len(msg)}, 含白名单/浏览器建议与现场片段")

if _BP.exists():
    _BP.unlink()
print("=== 4. profile 锁这类非浏览器缺失错误 → 不盲目回退, 原样抛出 ===")
lock = RuntimeError("ProcessSingleton: user data directory is already in use, "
                    "please close the browser and try again")
pw = FakePW({None: lock, "msedge": "OK_EDGE"})
try:
    TB.launch_browser(pw, "/tmp/prof")
    raise AssertionError("应原样抛出, 不换成 Edge(换了也解不了 profile 冲突)")
except RuntimeError as e:
    assert "already in use" in str(e), str(e)[:120]
    assert len(pw.chromium.calls) == 1, f"不该继续尝试其他候选: {pw.chromium.calls}"
    print(f"  ✓ 只尝试 1 次即抛出(未掩盖真因)")

print("\n✅ 浏览器启动回退测试全部通过")
