# -*- coding: utf-8 -*-
"""浏览器"被连续关闭"的恢复路径修复单测 (2026-10-12 客户机实证)。

现场: 客户机界面 = "自动化浏览器被连续关闭(已自动重开 2 次仍失败)" +
      "Locator.count: Target page, context or browser has been closed
       Browser logs: <launching> ...chrome.exe" (新包, 已知收尾文案)。

代码侧确定的缺陷链: Windows 分支"杀残留进程"后**不等退出**就立刻启动新浏览器
→ 被杀的旧进程还没释放 profile → 新 Chromium 起来就死 → "自动重开"连开连死,
把一次死亡放大成"连续关闭"。修复三件套 + 取证:
  ① 杀进程序列 = 顶层(命令行含 profile) + 全部后代(renderer/gpu 子进程可能不带 profile);
  ② 杀完 wait_profile_free 等到真正释放才返回;
  ③ 重开后 wait_browser_ready 验证真的活着, 没活就关掉再试一次;
  ④ browser_death_forensics 落一份"死亡现场"文件(进程表+崩溃事件), 供区分外部杀/自身崩。
"""
import inspect
import os
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import taobao as T   # noqa: E402


def test_select_kill_set():
    prof = r"C:\Users\demo\AppData\Roaming\MerchantReportClient\browser-profile"
    table = [
        (100, 1, rf'...\chrome.exe --user-data-dir="{prof}" --disable-blink-features'),
        (101, 100, r'...\chrome.exe --type=renderer'),            # 后代, 命令行里无 profile
        (102, 101, r'...\chrome.exe --type=utility'),             # 孙代
        (200, 1, r'...\python.exe merchant_report.task_main'),    # 无关
        (300, 200, r'...\cmd.exe'),                               # 无关的后代
    ]
    got = T.select_profile_kill_pids(table, prof)
    assert got == [100, 101, 102], got
    # 大小写不敏感(Windows 路径)
    got2 = T.select_profile_kill_pids([(7, 1, 'CHROME.EXE --USER-DATA-DIR=' + prof.upper())], prof)
    assert got2 == [7], got2
    print("✅ 杀进程序列: 命中顶层 + 全部后代, 不碰无关进程")


def test_wait_profile_free():
    seq = {"n": 0}

    def probe(_p):
        seq["n"] += 1
        return [123] if seq["n"] < 3 else []
    ok, left = T.wait_profile_free("X", timeout_s=5, poll_s=0.01, probe=probe)
    assert ok and left == 0 and seq["n"] == 3, (ok, left, seq)
    ok2, left2 = T.wait_profile_free("X", timeout_s=0.05, poll_s=0.01, probe=lambda p: [1, 2])
    assert (not ok2) and left2 == 2
    ok3, left3 = T.wait_profile_free("X", timeout_s=0.05, poll_s=0.01, probe=lambda p: None)
    assert (not ok3) and left3 == -1
    print("✅ wait_profile_free: 等到释放 / 超时报数 / 探测不可用, 三种情形都对")


def test_wait_browser_ready():
    class DeadCtx:
        @property
        def pages(self):
            raise RuntimeError("Target closed")

    class AliveCtx:
        class P:
            def evaluate(self, *_a):
                return 1

        @property
        def pages(self):
            return [self.P()]

    assert T.wait_browser_ready(DeadCtx(), timeout_s=0.3, poll_s=0.05) is False
    assert T.wait_browser_ready(AliveCtx(), timeout_s=0.5, poll_s=0.05) is True
    print("✅ wait_browser_ready: 死 context=False, 活 context=True")


def test_forensics_file():
    import re
    with tempfile.TemporaryDirectory() as d:
        p = T.browser_death_forensics(d, d, err_text="Target closed 测试现场")
        assert p and os.path.exists(p), p
        txt = open(p, encoding="utf-8").read()
        for kw in ("浏览器死亡现场", "Target closed 测试现场", "相关进程", "Application 错误事件", "被外部结束"):
            assert kw in txt, (kw, txt[:300])
        assert re.search(r"browser_diag_\d{8}_\d{6}\.txt$", os.path.basename(p))
    print("✅ 取证文件: 生成且含全部关键段落")


def test_format_diag_filter():
    rows = [(1, 0, "10:00", r"C:\x\chrome.exe --type=renderer"),
            (2, 0, "10:00", r"C:\x\notepad.exe")]
    txt = T.format_browser_diag("now", "err", "prof", rows, ["evt-line"], "Windows")
    assert "chrome.exe" in txt and "notepad.exe" not in txt and "evt-line" in txt
    print("✅ 取证格式: 只收 chrome/msedge 家族, 事件行照录")


def test_wiring_source():
    src_cl = inspect.getsource(T._cleanup_stale_browser)
    assert "_other_live_task_pids" in src_cl, "双开防护闸不许丢(CI 冒烟同查)"
    assert "wait_profile_free" in src_cl, "杀完必须等 profile 真正释放(本次修复点)"
    assert "select_profile_kill_pids" in src_cl, "要按'顶层+后代'序列杀"
    from merchant_report import orchestrator as O
    src_rt = inspect.getsource(O.run_task)
    assert "wait_browser_ready" in src_rt, "重开必须校验'真的活着'"
    assert "browser_death_forensics" in src_rt, "首次死亡要落现场取证"
    assert "watch_browser" in src_rt, "要挂断开监听"
    print("✅ 接线检查: 清理等待 + 重开校验 + 现场取证 + 断开监听 都在位")


def main():
    test_select_kill_set()
    test_wait_profile_free()
    test_wait_browser_ready()
    test_forensics_file()
    test_format_diag_filter()
    test_wiring_source()
    print("\n✅ 浏览器恢复路径(Windows 竞态修复+取证)全绿")


if __name__ == "__main__":
    main()
