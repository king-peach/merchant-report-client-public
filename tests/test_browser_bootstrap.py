# -*- coding: utf-8 -*-
"""浏览器组件引导测试(bootstrap): 已就绪不下载 / 缺失时走镜像下载 / 失败不阻塞任务

背景: 打包 Chromium 会让安装包 251MB, 超 GitHub 单文件 100MB 硬限制(推 build 分支被拒),
所以改为首次运行从国内镜像下载。关键要求: 下载失败不能让任务挂掉——launch_browser 会回退系统 Edge。
"""
import subprocess
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "kernel"))

from merchant_report import bootstrap as B
import merchant_report.browser_pref as _BP
import tempfile as _tf
from pathlib import Path as _PP

# 隔离浏览器偏好记忆文件: _verify_cached 失败会调 mark_verify_failed, 别写进真实用户目录
_BPF = _PP(_tf.mkdtemp()) / "browser_pref.json"
_BP._path = lambda: _BPF


class FakeSink:
    def __init__(self):
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))


def with_fakes(exe="", rc=0, popen_raises=None, after_exe=None, chunks=None, timeout_s=None,
               verify_ok=True, mark_exists=False):
    """替换 chromium_path / subprocess.Popen / verify_browser; 返回 (sink, 记录)"""
    import tempfile
    from pathlib import Path as _P
    import merchant_report.platforms.taobao as _TB
    sink = FakeSink()
    rec = {"calls": [], "killed": False, "verify_calls": 0}
    mark = _P(tempfile.mkdtemp()) / ".browser_verified"

    def _fake_verify(exe_="", timeout_ms=45000):
        rec["verify_calls"] += 1
        return (True, "") if verify_ok else (False, "Timeout 45000ms exceeded. Call log: <launching> ...")

    _TB.verify_browser = _fake_verify
    B._verify_marker = lambda exe_: mark
    seq = iter([exe, after_exe if after_exe is not None else exe])

    B.chromium_path = lambda: next(seq, "")
    if chunks is None:
        chunks = ["Downloading Chromium 140.0 from mirror\n", "|####      | 40% of 130.2 MiB",
                  "|########  | 90% of 130.2 MiB", "Installation complete\n"]

    class FakeStdout:
        def __init__(self, items):
            self.items, self.i = list(items), 0

        def read(self, n=256):
            if self.i >= len(self.items):
                return ""
            c = self.items[self.i]
            self.i += 1
            return c

    class FakePopen:
        def __init__(self, cmd, **kw):
            rec["calls"].append({"cmd": cmd, "host": (kw.get("env") or {}).get("PLAYWRIGHT_DOWNLOAD_HOST"),
                                 "browsers": (kw.get("env") or {}).get("PLAYWRIGHT_BROWSERS_PATH")})
            if popen_raises:
                raise popen_raises
            self.stdout = FakeStdout(chunks)
            self._rc = rc

        def wait(self, timeout=None):
            return self._rc

        def kill(self):
            rec["killed"] = True

    B.subprocess.Popen = lambda cmd, **kw: FakePopen(cmd, **kw)
    return sink, rec


def _unused_with_fakes_run(exe="", run_rc=0, run_raises=None, after_exe=None):
    pass


print("=== 1. 已就绪 → 不下载, 直接放行 ===")
sink, rec = with_fakes(exe="C:/x/chrome.exe", mark_exists=True)
B._verify_marker("x").write_text("C:/x/chrome.exe", encoding="utf-8")
ok, info = B.ensure_browser(sink)
assert ok and info == "C:/x/chrome.exe"
assert rec["verify_calls"] == 0, "已验过的版本不该再自检"
assert rec["calls"] == [], "已就绪却触发下载"
assert any("已就绪" in m for _, m in sink.logs)
print(f"  ✓ {[m for _, m in sink.logs][0]}")

print("=== 2. 缺失 → 从国内镜像下载(不打包进安装包) ===")
sink, rec = with_fakes(exe="", rc=0, after_exe="C:/appdata/ms-playwright/chrome.exe")
ok, info = B.ensure_browser(sink)
assert ok, sink.logs
assert len(rec["calls"]) == 1
call = rec["calls"][0]
assert call["cmd"][1:3] == ["-m", "playwright"] and call["cmd"][3:5] == ["install", "chromium"], call["cmd"]
assert "npmmirror" in (call["host"] or ""), f"未使用国内镜像: {call['host']}"
assert any("首次运行需下载" in m for _, m in sink.logs)
print(f"  ✓ 镜像={call['host']}")

print("=== 3. 平台语义: Windows 指定用户数据目录, macOS/Linux 沿用默认缓存 ===")
import os as _os
saved = _os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
import types as _types
try:
    # 模拟 Windows: 应把浏览器目录指到用户数据目录(不打进安装包)
    B.os = _types.SimpleNamespace(name="nt", environ=_os.environ)
    d = B.prepare_env()
    assert "ms-playwright" in d, f"Windows 下未指定浏览器目录: {d}"
    win_dir = d
    # 模拟 macOS: 不设该变量(沿用 Playwright 默认缓存, 开发机不重复下载)
    _os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    B.os = _types.SimpleNamespace(name="posix", environ=_os.environ)
    d2 = B.prepare_env()
    assert d2 == "", f"非 Windows 不该改默认缓存路径: {d2}"
finally:
    B.os = _os
    _os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    if saved:
        _os.environ["PLAYWRIGHT_BROWSERS_PATH"] = saved
print(f"  ✓ Windows → …{win_dir[-28:]}")
print(f"  ✓ macOS/Linux → 默认缓存(不改动)")

print("=== 4. 下载失败 → 告警 + 返回 False(不抛异常, 任务继续) ===")
sink, rec = with_fakes(exe="", rc=1)
ok, reason = B.ensure_browser(sink)
assert ok is False and "退出码" in reason, (ok, reason)
warn = [m for lv, m in sink.logs if lv == "warn"]
assert warn and "回退系统" in warn[0], warn
print(f"  ✓ 不抛异常, 告警: {warn[0][:52]}…")

print("=== 5. 下载进程异常(网络/超时) → 同样不阻塞 ===")
sink, rec = with_fakes(exe="", popen_raises=OSError("无法启动进程"))
ok, reason = B.ensure_browser(sink)
assert ok is False
warn = [m for lv, m in sink.logs if lv == "warn"]
assert warn and "回退系统" in warn[0], warn
print(f"  ✓ 异常被吞并降级: {warn[0][:52]}…")

print("=== 6. 下载完成但找不到可执行文件 → 也算失败(不谎报成功) ===")
sink, rec = with_fakes(exe="", rc=0, after_exe="")
ok, reason = B.ensure_browser(sink)
assert ok is False and "未找到" in reason, (ok, reason)
print(f"  ✓ {reason}")


print("=== 7. 下载过程实时回报进度(否则界面几分钟没动静, 用户以为卡死) ===")
sink, rec = with_fakes(exe="", rc=0, after_exe="C:/x/chrome.exe")
ok, info = B.ensure_browser(sink)
assert ok, sink.logs
progress = [m for _, m in sink.logs if "下载中" in m]
assert progress, f"没有进度回报: {[m for _, m in sink.logs]}"
assert any("40%" in m or "90%" in m for m in progress), progress
assert any("自动下载, 无需你手动操作" in m for _, m in sink.logs), "未说明是自动下载"
assert any("以后不再下载" in m for _, m in sink.logs), "未说明只需一次"
print(f"  ✓ 进度回报 {len(progress)} 次: {progress[:3]}")
print(f"  ✓ 开头明确告知: 自动下载, 无需手动操作")

print("=== 8. 下载超时 → 杀掉进程 + 降级(不无限等) ===")
sink, rec = with_fakes(exe="", chunks=["starting download now\n"])
ok, reason = B.ensure_browser(sink, timeout_s=-1)   # -1: 已过期, 立刻触发超时
assert ok is False and reason == "下载超时", (ok, reason)
assert rec["killed"] is True, "超时应杀掉下载进程"
warn = [m for lv, m in sink.logs if lv == "warn"]
assert any("超时" in m for m in warn), warn
print(f"  ✓ 已杀进程并降级: {warn[0][:46]}…")


print("=== 9. 浏览器存在但启动不了(杀软拦截/文件损坏) → 自检拦住并降级 ===")
sink, rec = with_fakes(exe="C:/appdata/ms-playwright/chrome.exe", verify_ok=False)
ok, reason = B.ensure_browser(sink)
assert ok is False and "自检未通过" in reason, (ok, reason)
assert rec["verify_calls"] == 1, "应做一次自检"
warn = [m for lv, m in sink.logs if lv == "warn"]
assert any("自检未通过" in m for m in warn), warn
assert any("原因" in m for m in warn), warn
print(f"  ✓ 不再等 3 分钟超时才报错: {[m for m in warn if '自检未通过' in m][0][:46]}…")


print("=== 12. 自检失败不写 bundled_broken(只有真实启动失败才算不可用) ===")
import json as _json
sink, rec = with_fakes(exe="C:/x/chrome.exe", verify_ok=False)
if _BPF.exists():
    _BPF.unlink()
ok, reason = B.ensure_browser(sink)
assert ok is False, (ok, reason)
assert _BP.bundled_broken() is False, "自检失败不该把自带 Chromium 判死(曾因此全走系统 Chrome)"
assert _BP.verify_failed_recently() is True, "应记录已验过失败, 以免每次白等 45s"
print("  ✓ 只记冷却, 不写不可用 → 下次仍会先试内置 Chromium")

print("\n✅ 浏览器组件引导测试全部通过")
