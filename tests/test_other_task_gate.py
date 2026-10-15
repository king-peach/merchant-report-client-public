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


def test_gate_five_cases():
    orig = T._other_live_task_pids
    orig_ppid = O.os.getppid
    try:
        # ① 无另一个任务 → 直接继续, 不打扰
        T._other_live_task_pids = lambda: []
        s = Sink()
        assert O._other_task_gate(s, Human(None)) is True
        assert not any("PANEL" in x for x in s.logs)

        # ② 同一窗口的兄弟进程(ppid == 本进程的父进程号) → 不弹窗 + 有 info + True
        #    2026-10-15 真机误报: "一键跑所有平台"串行衔接, 前一个平台进程收尾中 →
        #    旧版每切一个平台就弹一次 120s 恐吓框。这里是核心回归点。
        O.os.getppid = lambda: 7777
        T._other_live_task_pids = lambda: [(12345, 7777)]
        s2 = Sink()
        assert O._other_task_gate(s2, Human("结束本次（只留一个窗口后重跑）")) is True, \
            "兄弟进程绝不能被当成另一个窗口(否则一键队列被自己打断)"
        assert not any("PANEL" in x for x in s2.logs), f"兄弟进程不该弹窗: {s2.logs}"
        assert any("串行队列正常衔接" in x for x in s2.logs), s2.logs

        # ③ 非兄弟(真正另一个窗口) + 用户选「结束本次」→ False
        T._other_live_task_pids = lambda: [(12345, 4242)]
        s3 = Sink()
        assert O._other_task_gate(s3, Human("结束本次（只留一个窗口后重跑）")) is False
        assert any("另一个" in x for x in s3.logs), s3.logs

        # ④ 非兄弟 → 选「仍然继续试试」→ True; 无响应(无人值守) → True + warn
        T._other_live_task_pids = lambda: [(999, 4242)]
        assert O._other_task_gate(Sink(), Human("仍然继续试试")) is True
        s4b = Sink()
        assert O._other_task_gate(s4b, Human(None)) is True
        assert any("未收到响应" in x for x in s4b.logs), s4b.logs

        # ⑤ 探测不可用(None) → 继续、不弹窗
        s5 = Sink()
        T._other_live_task_pids = lambda: None
        assert O._other_task_gate(s5, Human(None)) is True
        assert not any("PANEL" in x for x in s5.logs), s5.logs
    finally:
        T._other_live_task_pids = orig
        O.os.getppid = orig_ppid
    print("✅ 双开冲突门: 五情形(无冲突/兄弟/选结束/选继续或超时/探测不可用)全对")


def test_split_foreign_pure():
    """纯函数拆分: 兄弟 / 非兄弟 / ppid 未知(-1 保守当非兄弟) / 旧格式裸 pid / 空。"""
    # 兄弟(ppid == mine)全归 siblings
    assert O._split_foreign_task_pids([(100, 555), (101, 555)], 555) == ([], [(100, 555), (101, 555)])
    # 非兄弟全归 foreign
    assert O._split_foreign_task_pids([(100, 999)], 555) == ([(100, 999)], [])
    # ppid 未知(-1) → 非兄弟(保守: 宁可多问一句, 不可漏报真双开)
    f, s = O._split_foreign_task_pids([(100, -1), (101, 555)], 555)
    assert f == [(100, -1)] and s == [(101, 555)], (f, s)
    # 旧格式裸 pid → 当未知父进程(非兄弟)
    f2, s2 = O._split_foreign_task_pids([100], 555)
    assert f2 == [(100, -1)] and s2 == [], (f2, s2)
    # 混在一起: 两者都有
    f3, s3 = O._split_foreign_task_pids([(1, 555), (2, 999)], 555)
    assert f3 == [(2, 999)] and s3 == [(1, 555)], (f3, s3)
    # 空/None → 都空
    assert O._split_foreign_task_pids([], 555) == ([], [])
    assert O._split_foreign_task_pids(None, 555) == ([], [])
    print("✅ 纯函数拆分: 兄弟/非兄弟/未知ppid/旧格式/空 全部正确")


def test_wiring_source():
    src = inspect.getsource(O.run_task)
    assert "_other_task_gate(sink, human)" in src, "run_task 必须接入双开冲突门"
    i_gate = src.find("_other_task_gate(sink, human)")
    i_date = src.find("_template_date_gate(")
    assert i_gate != -1 and i_date != -1 and i_gate < i_date, "冲突门要在日期门之前(先讲根本冲突)"
    assert "E_OTHER_TASK_RUNNING" in src
    print("✅ 接线检查: 冲突门在 run_task 早期、日期门之前")


def main():
    test_gate_five_cases()
    test_split_foreign_pure()
    test_wiring_source()
    print("\n✅ 双开/旧版残留冲突提示门 全绿")


if __name__ == "__main__":
    main()
