# -*- coding: utf-8 -*-
"""M1 单测: 模板占用检测 / 阶梯等待重试 / 开工等待 —— **全瞬时化**(假 sleep, 绝不真等几十秒)。

覆盖 2026-09-13/09-14 真机两条: ①保存阶段被 Excel/WPS 占着 → 旧实现 6 秒就整批放弃(零写入);
②开工检查一看被占就失败, 用户当场关掉文件其实就能跑。
"""
import inspect
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kernel"))

import merchant_report.fill as fill


def _fresh_cache():
    """绕过 8 秒结果缓存(否则第二个用例会读到上一个用例的进程列表)。"""
    fill._OFFICE_PROC_CACHE = (0.0, None)


class FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout


print("=== A. find_office_processes: 解析 tasklist CSV(含干扰行) ===")
_CSV = ('"EXCEL.EXE","1234","Console","1","123,456 K"\n'
        '"chrome.exe","4321","Console","1","88,000 K"\n'
        '"et.exe","5678","Console","1","60,000 K"\n'
        '"WPS.EXE","9012","Console","1","10,000 K"\n')
_seen = {}


def _fake_run(cmd, **kw):
    _seen["cmd"], _seen["kw"] = cmd, kw
    return FakeCompleted(_CSV)


_orig_name, _orig_run = fill.os.name, fill.subprocess.run
fill.os.name = "nt"
fill._sleep = lambda s: None
fill.subprocess.run = _fake_run
_fresh_cache()
procs = fill.find_office_processes()
assert procs == [("EXCEL.EXE", 1234), ("et.exe", 5678), ("WPS.EXE", 9012)], procs
assert _seen["cmd"][:2] == ["tasklist", "/FO"] and "/NH" in _seen["cmd"], _seen["cmd"]
assert _seen["kw"].get("timeout") == 6.0, _seen["kw"]
print(f"  解析 {procs} ✓ (chrome.exe 干扰行被过滤; CREATE_NO_WINDOW={_seen['kw'].get('creationflags')})")

print("=== B. 缓存生效(8 秒内不重复起 tasklist) ===")
_calls = []
fill.subprocess.run = lambda cmd, **kw: (_calls.append(cmd), FakeCompleted(_CSV))[1]
fill.find_office_processes()
assert _calls == [], _calls
print("  第二次调用命中缓存, 未再起子进程 ✓")

print("=== C. 非 Windows → [] (不探测) ===")
fill.os.name = "posix"
_fresh_cache()
assert fill.find_office_processes() == []
print("  posix → [] ✓")

print("=== D. 探测异常/超时 → None (与'没找到'区分) ===")
fill.os.name = "nt"
fill.subprocess.run = lambda cmd, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, 6.0))
_fresh_cache()
assert fill.find_office_processes() is None
assert fill.occupancy_hint() == "（进程探测不可用）"
print("  异常 → None, 文案=进程探测不可用 ✓")

print("=== E. occupancy_hint 三态文案 ===")
fill.subprocess.run = _fake_run
_fresh_cache()
h = fill.occupancy_hint()
assert "EXCEL.EXE(PID 1234)" in h and "托盘" in h, h
fill.subprocess.run = lambda cmd, **kw: FakeCompleted('"chrome.exe","1","Console","1","1 K"\n')
_fresh_cache()
assert "未检测到 Excel/WPS 进程" in fill.occupancy_hint()
print("  有点名进程 / 未检测到 / 探测不可用 三态 ✓")

print("=== F. ensure_writable: 占用提示必须在最前面(下游 [:120] 截断) ===")
import builtins
fill.os.name = "nt"
fill.subprocess.run = _fake_run
_fresh_cache()


def _deny_open(p, mode="r"):
    raise PermissionError(13, "Permission denied", str(p))


_b2, _exists, _access = builtins.open, fill.os.path.exists, fill.os.access
try:
    fill.os.path.exists = lambda p: True
    fill.os.access = lambda p, m: True
    builtins.open = _deny_open
    fill_ok, fill_why = fill.ensure_writable("/x/模板.xlsx")
finally:
    fill.os.path.exists, fill.os.access, builtins.open = _exists, _access, _b2
assert fill_ok is False, fill_ok
assert fill_why.startswith("模板正被其他程序占用"), fill_why
assert "EXCEL.EXE(PID 1234)" in fill_why[:120], fill_why[:120]
print(f"  前 120 字内已含占用进程名 ✓ → {fill_why[:60]}…")

print("=== G. wait_writable: 等待中释放 → True 且日志≥2 条 ===")
fill._sleep = lambda s: None
_seq = [False, False, True]
_calls_g = []
fill.ensure_writable = lambda p, what="模板": (_calls_g.append(1), (_seq.pop(0), "占用"))[1]
_logs = []
_waits = []
ok = fill.wait_writable("/x/t.xlsx", "模板", total_s=60, poll_s=5,
                        log=lambda m: _logs.append(m), _sleep_fn=lambda s: _waits.append(s))
assert ok is True, ok
assert len(_logs) >= 2 and _waits == [5, 5, 5], (_logs, _waits)
assert "已可写" in _logs[-1] and "等待 15 秒后释放" in _logs[-1], _logs[-1]
print(f"  {len(_logs)} 条日志 / sleep={_waits} → 释放后自动继续 ✓")

print("=== H. wait_writable: 一直占用 → False 且 sleep 次数 == 上限 ===")
fill.ensure_writable = lambda p, what="模板": (False, "占用")
_waits2 = []
ok2 = fill.wait_writable("/x/t.xlsx", total_s=10, poll_s=5, log=lambda m: None,
                         _sleep_fn=lambda s: _waits2.append(s))
assert ok2 is False, ok2
assert len(_waits2) == 2, _waits2
print(f"  total_s=10/poll_s=5 → sleep {len(_waits2)} 次, 返回 False ✓")

print("=== I. wait_writable: 探测自身异常不许炸任务 ===")
_orig_ensure = fill.ensure_writable          # 记下真身, 后面 save_wb 用例要还原
_fresh_cache()
fill.os.name = "nt"


def _boom(p, what="模板"):
    raise RuntimeError("探测炸了")


fill.ensure_writable = _boom
assert fill.wait_writable("/x/t.xlsx", total_s=10, poll_s=5, _sleep_fn=lambda s: None) is True
print("  异常 → True(放行, 交回原逻辑) ✓")

print("=== J. save_wb: 前 2 次占用后成功(阶梯等待 + on_retry) ===")
fill.ensure_writable = _orig_ensure
fill.os.name = "nt"
fill.subprocess.run = _fake_run
_fresh_cache()


class FakeWb:
    def __init__(self, fail_times):
        self.fail_times, self.n = fail_times, 0

    def save(self, path):
        self.n += 1
        if self.n <= self.fail_times:
            raise PermissionError(13, "Permission denied", str(path))


_slept, _retries = [], []
fill._sleep = lambda s: _slept.append(s)
assert fill.save_wb(FakeWb(2), "/x/t.xlsx", on_retry=_retries.append) is True
assert len(_retries) == 2, _retries
assert _slept == [2, 5], _slept
assert "第 1/6 次重试" in _retries[0] and "第 2/6 次重试" in _retries[1], _retries
assert "已等待约 2 秒" in _retries[0] and "已等待约 7 秒" in _retries[1], _retries
print(f"  重试 {len(_retries)} 条 / sleep={_slept} → 最终成功 ✓")

print("=== K. save_wb: 一直占用 → RuntimeError 含 '82 秒' 与占用提示 ===")
_slept2 = []
fill._sleep = lambda s: _slept2.append(s)
try:
    fill.save_wb(FakeWb(99), "/x/t.xlsx")
    raise AssertionError("应当抛 RuntimeError")
except RuntimeError as e:
    msg = str(e)
assert "82 秒" in msg and "EXCEL.EXE(PID 1234)" in msg, msg
assert sum(_slept2) == 82 and len(_slept2) == 6, _slept2
print(f"  抛出: {msg.splitlines()[-1][:56]}… ✓ (共等待 {sum(_slept2)} 秒 / {len(_slept2)} 次)")

print("=== L. 非占用类 OSError 仍然直接抛(不吞其它错误) ===")
try:
    fill.save_wb(type("W", (), {"save": lambda self, p: (_ for _ in ()).throw(OSError(28, "No space"))})(), "/x/t")
    raise AssertionError("应当抛 OSError")
except OSError as e:
    assert e.errno == 28, e
print("  errno 28 原样抛出 ✓")

print("=== M. on_retry 已透传到 fill_many / fill_template / save_wb ===")
for fn in (fill.save_wb, fill.fill_many, fill.fill_template):
    assert "on_retry" in inspect.signature(fn).parameters, fn.__name__
    print(f"  {fn.__name__} 签名含 on_retry ✓")

print("=== N. SAVE_RETRY_DELAYS 阶梯文案常量 ===")
assert fill.SAVE_RETRY_DELAYS == (2, 5, 10, 15, 20, 30), fill.SAVE_RETRY_DELAYS
assert fill.SAVE_RETRY_TOTAL_S == 82, fill.SAVE_RETRY_TOTAL_S
assert "excel.exe" in fill.OFFICE_PROC_NAMES and "wpscloudsvr.exe" in fill.OFFICE_PROC_NAMES
print(f"  delays={fill.SAVE_RETRY_DELAYS} 合计={fill.SAVE_RETRY_TOTAL_S}s ✓")

print("\n✅ M1 占用检测/阶梯重试单测全部通过")
