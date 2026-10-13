# -*- coding: utf-8 -*-
"""HumanBridge.wait 超时兜底单测 (2026-10-13 客户机"残留内核/卡死"机制根治)。

背景: 此前面板等待是裸 readline 无限阻塞("壳负责超时"但壳层从未实现) —— 用户不点
面板 → 内核永久卡死 = 残留内核, 后续运行全被"另一个任务在运行"挡住(18492 事故链)。
本测试钉死: ①无人应答 → 超时返回 None(不许真等到天长地久); ②正常回复照常;
③默认上限存在 + 读行走 daemon 线程(Windows 管道无 select, 线程超时是唯一稳妥解)。
"""
import inspect
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.events import HumanBridge          # noqa: E402
from merchant_report import events as E                 # noqa: E402


class _NeverAnswers:
    """永不回行的 stdin 替身(模拟: 面板在, 但没人点)。"""

    def readline(self):
        time.sleep(30)
        return ""


class _OneShot:
    def __init__(self, line):
        self._line = line

    def readline(self):
        return self._line


def test_timeout_returns_none():
    old = sys.stdin
    sys.stdin = _NeverAnswers()
    try:
        t0 = time.time()
        r = HumanBridge(from_stdin=True).wait({"type": "need_human"}, timeout_s=0.5)
        dt = time.time() - t0
        assert r is None, r
        assert dt < 5, f"超时未生效(等了 {dt:.1f}s)"
        print(f"✅ 超时: 无人应答 → {dt:.1f}s 内返回 None(调用方兜底接管)")
    finally:
        sys.stdin = old


def test_normal_response():
    old = sys.stdin
    sys.stdin = _OneShot(json.dumps({"choice": "继续"}, ensure_ascii=False) + "\n")
    try:
        r = HumanBridge(from_stdin=True).wait({"type": "need_human"}, timeout_s=5)
        assert r == {"choice": "继续"}, r
        print("✅ 正常: 收到回复照常返回")
    finally:
        sys.stdin = old


def test_wiring():
    assert getattr(E, "DEFAULT_WAIT_TIMEOUT_S", 0) >= 300, "必须有默认上限(不许无限等待)"
    src = inspect.getsource(E.HumanBridge.wait)
    assert "daemon=True" in src, "读行必须放 daemon 线程(不阻塞进程退出)"
    assert "queue" in src and "timeout" in src, "必须走队列超时"
    # 三个关键等待点都传了 timeout(双开门 120 / 日期门 600 / 卡点面板 DEFAULT_TIMEOUT_S)
    from merchant_report import orchestrator as O
    from merchant_report import failure_ui as F
    so, sf = inspect.getsource(O), inspect.getsource(F)
    assert "timeout_s=120" in so and "timeout_s=600" in so, "双开门/日期门未传等待上限"
    assert "timeout_s=DEFAULT_TIMEOUT_S" in sf, "卡点面板未传等待上限"
    print("✅ 接线: 默认上限 + daemon 线程 + 三个关键等待点全部传上限")


def main():
    test_timeout_returns_none()
    test_normal_response()
    test_wiring()
    print("\n✅ HumanBridge 超时兜底 全绿")


if __name__ == "__main__":
    main()
