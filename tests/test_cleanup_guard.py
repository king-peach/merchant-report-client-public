# -*- coding: utf-8 -*-
"""双开防护/清理闸门单测: 把 2026-10-11 真机事故修出来的三条不变量钉死

背景(事故复盘):
  客户机跑任务时"自动化浏览器被关闭"(E_BROWSER_DEAD), 且观察到两个图标。
  排查发现两件事:
  ① 启动时的"残留浏览器清理"把命令行里带本 profile 的进程**无差别强杀** ——
     两个窗口同时跑任务时, 会把对方正在用的浏览器杀掉 → 循环互相关闭;
  ② 更早的一个静默 bug: 本模块自 2026-09-21 Windows 打包起漏 `import subprocess`,
     清理函数引用 subprocess 直接 NameError, 被 except 静默吞掉 → **从未真正执行过**。
     修 ② 的同时必须补上"先探活、有人就不杀"的闸, 否则等于把 ① 重新激活。

本测试钉死的规则:
  A. `import subprocess` 不许再丢(丢了 = 整段清理静默空转, 谁都不会发现);
  B. 探测到"还有另一个任务在跑" → 一个进程都不杀, 且给用户留提示;
  C. 探测不可用(不确定) → 同样不杀(安全侧: 宁可不清理, 不误杀);
  D. 确认没有其它任务 → 才执行清理(有 profile 路径精确匹配, 不碰用户自己的浏览器);
  E. 探活结果解析: 自己(当前进程)不算"其它任务"。
"""
import os
import sys
import inspect
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "kernel"))

import merchant_report.platforms.taobao as TB

# ---------- A. 回归守卫: subprocess import 不许再丢 ----------
print("=== A. import 回归守卫(曾漏 → 清理静默空转三周) ===")
_src_path = _P(__file__).resolve().parents[1] / "kernel" / "merchant_report" / "platforms" / "taobao.py"
_src = _src_path.read_text(encoding="utf-8")
assert "import subprocess" in _src, "taobao.py 又漏 import subprocess — 清理会 NameError 静默空转!"
assert "_other_live_task_pids" in inspect.getsource(TB._cleanup_stale_browser), \
    "清理函数里没有探活闸 — 双开时又会互相关闭"
assert "_cleanup_stale_browser(profile_dir, sink=sink)" in inspect.getsource(TB.launch_browser), \
    "launch_browser 没把 sink 传给清理函数 — 跳过清理时用户看不到提示"
print("  ✓ import 在位 / 探活闸在位 / sink 接线在位")


# ---------- 测试替身 ----------
class FakeSink:
    def __init__(self):
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, str(msg)))


class FakeSP:
    """替身 subprocess: 记录调用, 永不真的执行命令(测试绝不能碰真实进程表)。"""
    calls = []

    @staticmethod
    def run(argv, **kw):
        FakeSP.calls.append(list(argv))

        class R:
            stdout = ""
        return R()


_real_probe = TB._other_live_task_pids
_real_sp = TB.subprocess
TB.subprocess = FakeSP
try:
    # ---------- B. 有另一个任务在跑 → 一个进程都不杀 ----------
    # 2026-10-15: 探活返回 [(pid, ppid)] —— 本函数只看"有没有"(判空), 兄弟也算"有"照样跳过。
    print("=== B. 检测到另一个任务在运行 → 一个进程都不杀 ===")
    FakeSP.calls.clear()
    TB._other_live_task_pids = lambda: [(12345, 999)]
    sink = FakeSink()
    r = TB._cleanup_stale_browser("/tmp/dual-guard-prof", sink=sink)
    assert r is False, f"应返回 False(跳过): {r}"
    assert FakeSP.calls == [], f"不许执行任何进程命令: {FakeSP.calls}"
    assert any("另一个" in m and "跳过" in m for _, m in sink.logs), sink.logs
    assert any("12345" in m for _, m in sink.logs), f"提示里要带 pid(不是元组): {sink.logs}"
    assert sink.logs[0][0] == "warn", sink.logs
    print(f"  ✓ 0 条进程命令; 提示: {sink.logs[0][1][:44]}…")

    # ---------- C. 探测不可用 → 保守不杀 ----------
    print("=== C. 探测不可用(不确定) → 同样不杀 ===")
    FakeSP.calls.clear()
    TB._other_live_task_pids = lambda: None
    sink = FakeSink()
    r = TB._cleanup_stale_browser("/tmp/dual-guard-prof", sink=sink)
    assert r is False and FakeSP.calls == [], (r, FakeSP.calls)
    assert any("无法确认" in m for _, m in sink.logs), sink.logs
    print("  ✓ 不确定就不动进程表(宁可不清理, 不误杀)")

    # ---------- D. 独苗 → 恢复清理动作 ----------
    print("=== D. 没有其它任务 → 才执行清理(有 profile 精确匹配) ===")
    FakeSP.calls.clear()
    TB._other_live_task_pids = lambda: []
    r = TB._cleanup_stale_browser("/tmp/dual-guard-prof", sink=None)
    assert r is True, f"应返回 True(已清理/无需清理): {r}"
    assert FakeSP.calls, "没有任何清理命令被调用 — 清理又空转了?"
    assert any("dual-guard-prof" in " ".join(map(str, c)) for c in FakeSP.calls), FakeSP.calls
    print(f"  ✓ 执行了 {len(FakeSP.calls)} 条命令, 且带 profile 路径(只杀本 profile 的残留)")

    # ---------- E. 探活解析: 新格式 (pid, ppid); 自己(当前进程)不算"其它任务" ----------
    # POSIX 分支: pgrep 拿 pid 列表 → 再一条 ps 补 ppid。
    print("=== E. 探活结果解析: (pid, ppid) 两列 + 自己不算其它 ===")

    class FakeSP2:
        @staticmethod
        def run(argv, **kw):

            class R:
                stdout = ""
            r = R()
            if "pgrep" in argv:
                # 包含: 自己 / 合法 pid / 空白 / 非数字噪声
                r.stdout = f"{os.getpid()}\n777\n  \nnot-a-pid\n"
            elif "ps" in argv:
                r.stdout = "777 555\n"
            return r

    TB.subprocess = FakeSP2
    TB._other_live_task_pids = _real_probe
    got = TB._other_live_task_pids()
    assert got == [(777, 555)], got
    assert all(isinstance(x, tuple) and len(x) == 2 for x in got), got
    print(f"  ✓ 解析出 [(777, 555)]: 自己(pid {os.getpid()})与非法值被过滤, ppid 由 ps 补齐")

    # 真探针(未打桩 subprocess, 只在本机真跑一次): 返回 None 或 [(pid, ppid)] 列表, 且不含本进程 pid
    print("=== E2. 真探针(本机真跑): 结构正确且不含本进程 ===")
    TB.subprocess = _real_sp
    real = _real_probe()
    assert real is None or (
        isinstance(real, list) and all(isinstance(x, tuple) and len(x) == 2
                                       and isinstance(x[0], int) and isinstance(x[1], int)
                                       for x in real)), real
    if real is not None:
        assert os.getpid() not in [p for p, _ in real], f"自己不该出现在探活结果里: {real}"
    print(f"  ✓ 真探针返回: {real if real is not None else 'None(探测不可用)'}")
finally:
    TB._other_live_task_pids = _real_probe
    TB.subprocess = _real_sp

print("\n✅ 双开防护/清理闸门单测全部通过")
