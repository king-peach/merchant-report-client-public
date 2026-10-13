# -*- coding: utf-8 -*-
"""「双开/旧版残留」冲突提示门单测 (2026-10-12 客户机实证)。

现场: 客户机"一打开就有两个客户端"; 两个客户端同时跑任务时共用同一个浏览器配置目录,
先启动的浏览器占住目录 → 后启动方新浏览器被顶死 → "浏览器被反复关闭、重开也没用"。
本门在任务早期(启动浏览器之前)检测"另一个任务进程", 把冲突讲清楚, 而不是进入死循环。

四情形: 无冲突→直接继续 / 检测到冲突+用户选结束→干净结束 / 选继续→放行 / 无响应→继续+warn。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report import orchestrator as O                 # noqa: E402
from merchant_report.platforms import taobao as T             # noqa: E402


class Sink:
    def __init__(self):
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append(str(msg))

    def need_human(self, *a, **k):
        self.logs.append("PANEL:" + str(a[:1]))


class Human:
    def __init__(self, answer):
        self.answer = answer

    def wait(self, obj, timeout_s=None):
        return {"choice": self.answer} if self.answer else None


def test_gate_four_cases():
    orig = T._other_live_task_pids
    try:
        # ① 无另一个任务 → 直接继续, 不打扰
        T._other_live_task_pids = lambda: []
        s = Sink()
        assert O._other_task_gate(s, Human(None)) is True
        assert not any("PANEL" in x for x in s.logs)

        # ② 检测到冲突 + 用户选「结束本次」→ False
        T._other_live_task_pids = lambda: [12345]
        s1 = Sink()
        assert O._other_task_gate(s1, Human("结束本次（只留一个窗口后重跑）")) is False
        assert any("另一个" in x for x in s1.logs), s1.logs

        # ③ 选「仍然继续试试」→ True
        T._other_live_task_pids = lambda: [999]
        assert O._other_task_gate(Sink(), Human("仍然继续试试")) is True

        # ④ 无响应(无人值守) → 继续 + warn; 探测不可用(None) → 继续
        assert O._other_task_gate(Sink(), Human(None)) is True
        s4 = Sink()
        T._other_live_task_pids = lambda: None
        assert O._other_task_gate(s4, Human(None)) is True
        assert not any("PANEL" in x for x in s4.logs)
    finally:
        T._other_live_task_pids = orig
    print("✅ 双开冲突门: 四情形(无冲突/选结束/选继续/无响应或探测不可用)全对")


def test_wiring_source():
    src = inspect.getsource(O.run_task)
    assert "_other_task_gate(sink, human)" in src, "run_task 必须接入双开冲突门"
    i_gate = src.find("_other_task_gate(sink, human)")
    i_date = src.find("_template_date_gate(")
    assert i_gate != -1 and i_date != -1 and i_gate < i_date, "冲突门要在日期门之前(先讲根本冲突)"
    assert "E_OTHER_TASK_RUNNING" in src
    print("✅ 接线检查: 冲突门在 run_task 早期、日期门之前")


def main():
    test_gate_four_cases()
    test_wiring_source()
    print("\n✅ 双开/旧版残留冲突提示门 全绿")


if __name__ == "__main__":
    main()
