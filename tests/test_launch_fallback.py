# -*- coding: utf-8 -*-
"""浏览器启动回退测试: 启动超时/缺失必须换候选; Windows 新默认=系统浏览器优先(2026-10-13 报告)

客户机排查报告(2026-10-13): 自带 Chromium 153 在"下载报表"环节 100% 进程内崩溃;
系统 Chrome 155 同机同流程 0 崩溃、完整任务跑通。→ Windows 默认顺序改为
[chrome → msedge → 自带], 自带只做兜底。本测试把"回退行为 + 默认顺序"钉死。
(跨平台: 顺序按运行平台取与 launch_browser 相同的表达式, 断言语义一致。)
"""
import os
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

TB._cleanup_stale_browser = lambda *a, **k: None   # 不碰进程表(sink=None 兼容)

# 与 launch_browser 内部同一表达式(Windows=系统优先; macOS=自带优先)
DEFAULT = ["chrome", "msedge", None] if os.name == "nt" else [None, "msedge", "chrome"]
D0, D1, D2 = DEFAULT
LBL = lambda ch: ch or "chromium(自带)"


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
    "chromium-1243\\chrome-win64\\chrome.exe --disable-blink-features=AutomationControlled\n"
    "- <launching>   userDataDir=\"...\"\n- <launching> <process did not exit>")

MISSING_ERR = RuntimeError(
    "BrowserType.launch_persistent_context: Executable doesn't exist at "
    "C:\\...\\ms-playwright\\chromium-1243\\chrome-win64\\chrome.exe\n"
    "Please run the following command to download new browsers")

if _BP.exists():
    _BP.unlink()
print(f"=== 1. 首选({LBL(D0)})启动超时 → 自动回退 {LBL(D1)}(不直接失败) ===")
pw = FakePW({D0: TIMEOUT_ERR, D1: "OK_SECOND", D2: "OK_LAST"})
ctx = TB.launch_browser(pw, "/tmp/prof")
assert ctx == {"ctx": D1, "profile": "/tmp/prof"}, ctx
tried = [c["channel"] for c in pw.chromium.calls]
assert tried == [D0, D1], tried
assert all(c["timeout"] == TB.LAUNCH_TIMEOUT_MS for c in pw.chromium.calls), pw.chromium.calls
print(f"  ✓ 尝试顺序 {[LBL(c) for c in tried]}, 均带 {TB.LAUNCH_TIMEOUT_MS}ms 超时")

if _BP.exists():
    _BP.unlink()
print(f"=== 2. 前两个候选都缺失 → 落到最后一个({LBL(D2)}) ===")
pw = FakePW({D0: MISSING_ERR, D1: MISSING_ERR, D2: "OK_LAST"})
ctx = TB.launch_browser(pw, "/tmp/prof")
assert ctx["ctx"] == D2, ctx
tried = [c["channel"] for c in pw.chromium.calls]
assert tried == [D0, D1, D2], tried
print(f"  ✓ 尝遍 {[LBL(c) for c in tried]} 后成功")

if _BP.exists():
    _BP.unlink()
print("=== 3. 全部候选都失败 → 报错含可操作建议 + 原始 Call log 片段 ===")
pw = FakePW({D0: TIMEOUT_ERR, D1: TIMEOUT_ERR, D2: TIMEOUT_ERR})
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
pw = FakePW({D0: lock, D1: "OK_SECOND"})
try:
    TB.launch_browser(pw, "/tmp/prof")
    raise AssertionError("应原样抛出, 不换候选(换了也解不了 profile 冲突)")
except RuntimeError as e:
    assert "already in use" in str(e), str(e)[:120]
    assert len(pw.chromium.calls) == 1, f"不该继续尝试其他候选: {pw.chromium.calls}"
    print("  ✓ 只尝试 1 次即抛出(未掩盖真因)")

if _BP.exists():
    _BP.unlink()
print("=== 5. 记忆自洽: 第二个候选成功后, 下次它排最前(不再白试第一个) ===")
beh = {D0: MISSING_ERR, D1: "OK_SECOND", D2: "OK_LAST"}
pw = FakePW(beh)
TB.launch_browser(pw, "/tmp/prof")
pw2 = FakePW(beh)
TB.launch_browser(pw2, "/tmp/prof")
assert pw2.chromium.calls[0]["channel"] == D1, f"第二次应直接用上次成功的候选: {pw2.chromium.calls}"
print(f"  第1次 [{[LBL(c['channel']) for c in pw.chromium.calls]}] → "
      f"第2次 [{[LBL(c['channel']) for c in pw2.chromium.calls]}]（直接命中）")

print("\n✅ 浏览器启动回退测试全部通过")
