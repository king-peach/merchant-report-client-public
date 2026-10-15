# -*- coding: utf-8 -*-
"""任务 M4 纯逻辑单测: ① 流量页日期窗口判定 ② 对账页键入"3 遍收敛"控制流。

真机探针(2026-10-15)钉死的两条:
  · 流量页日期控件窗口 = **最近 30 天 `[今天-30, 今天-1]`**(本地日期); 窗外日格带 `disabled`、
    点击无效, 且本页键入/React 合成注入全死 → 窗外的日期**客观取不到**(页面限制, 不是故障)。
    正确做法 = 动手**之前**判窗 + 秒级返回 + 一行日志说清楚, 绝不逐店反复试探。
  · 对账页两框有「区间跨度 ≤ 30 天」钳制 → 单遍键入目标日**必败**(中间态首日被改写成 末日−29);
    真机配方 = 同一目标连打**最多 3 遍**, 每遍读回, 第 2 遍即落 `[d,d]`。

本文件只测**纯函数/纯控制流**(不碰页面), 与页面交互的验收在 test_meituan_flow_date.py /
test_meituan_bill_date.py 里。
"""
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan as mt                           # noqa: E402
from merchant_report.platforms.meituan import MeituanAdapter                   # noqa: E402

TODAY = "2026-10-15"


def test_flow_date_reachable_fixed_today():
    """真机那一组(今日=10-15, 窗口=[9-15, 10-14]): 9-13/9-14 已滑出 → 不可取。"""
    r = lambda d: mt._flow_date_reachable(d, TODAY)
    assert r("2026-09-15") is True, "窗口起点(今天-30)可取"
    assert r("2026-09-14") is False, "滑出窗口(今天-31)不可取 —— 商圈TOP10 客观取不到"
    assert r("2026-09-13") is False, "再往前的日子同样不可取"
    assert r("2026-10-14") is True, "窗口终点(今天-1)可取"
    assert r("2026-10-15") is False, "今天不可取(控件最大只到昨天)"
    print("✅ 窗口(今日=10-15): 9-15✓ 9-14✗ 9-13✗ 10-14✓ 10-15✗")


def test_flow_date_reachable_bounds():
    """边界四连: 今天-30 ✓ / 今天-31 ✗ / 今天-1 ✓ / 今天 ✗; 并显式验证 min/max 文案。"""
    lo, hi = mt._flow_window_bounds(TODAY)
    assert (lo, hi) == ("2026-09-15", "2026-10-14"), (lo, hi)
    assert mt._flow_date_reachable(lo, TODAY) is True, "今天-30 = 窗口**含**边界"
    assert mt._flow_date_reachable("2026-09-14", TODAY) is False, "今天-31 出界"
    assert mt._flow_date_reachable(hi, TODAY) is True, "今天-1 = 上边界(含)"
    assert mt._flow_date_reachable(TODAY, TODAY) is False, "今天出界(控件只到昨天)"
    print(f"✅ 边界: {lo}✓ 今天-31✗ {hi}✓ 今天✗")


def test_flow_date_reachable_robust():
    """窗宽可配常量 / 坏输入 / 可注入 date 对象 / 缺省用本地今天 —— 都不许抛, 坏输入一律 False。"""
    assert mt.FLOW_WINDOW_DAYS == 30, "窗口宽度 = 30 天(真机探针值, 改它要有真机依据)"
    assert mt._flow_date_reachable("2026-09-15", datetime.date(2026, 10, 15)) is True, \
        "today 也接受 datetime.date"
    for bad in ("", "not-a-date", "2026-13-01", "2026-02-30", None, "2026/09/15"):
        assert mt._flow_date_reachable(bad, TODAY) is False, f"坏输入必须 False(宁缺勿假): {bad!r}"
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    assert mt._flow_date_reachable(yesterday.strftime("%Y-%m-%d")) is True, \
        "缺省 today = 本地今天 → 昨天在窗内"
    print("✅ 稳健性: 常量30 / date注入 / 坏输入全 False / 缺省本地今天")


def test_bill_typing_converged():
    """对账页读回判定: **两框都** == 目标日才算收敛(页面各种日期写法都要认; 坏值不算)。"""
    A = MeituanAdapter
    assert A._bill_typing_converged(["2026-09-13", "2026-09-13"], "2026-09-13") is True
    assert A._bill_typing_converged(["2026/09/13", "2026.9.13"], "2026-09-13") is True, "写法归一化"
    assert A._bill_typing_converged(["2026-09-16", "2026-09-16"], "2026-09-13") is False, \
        "真机中间态(被钳到 9-16)不算收敛"
    assert A._bill_typing_converged(["2026-09-13", "2026-10-15"], "2026-09-13") is False, \
        "只有一框对上不算(必须两框)"
    assert A._bill_typing_converged(["", ""], "2026-09-13") is False
    assert A._bill_typing_converged(["2026-09-13"], "2026-09-13") is False, "少一框不算"
    assert A._bill_typing_converged(None, "2026-09-13") is False
    print("✅ 收敛判定: 两框都==目标日(写法归一化); 半对/空/缺框一律不算")


def test_bill_retry_typing_control_flow():
    """3 遍循环控制流(注入 read 回调): ① 第1遍即成只打1遍 ② 真机序列 [[16,16],[d,d]] 第2遍成
    ③ 3 遍全败 → False。"""
    ad = MeituanAdapter.__new__(MeituanAdapter)
    date = "2026-09-13"

    # ① 第 1 遍就 [d,d] → 成功且**只打 1 遍**
    calls = []
    ok, rounds, vals = ad._bill_retry_typing(
        lambda: calls.append(1), lambda: ["2026-09-13", "2026-09-13"], date)
    assert ok is True and rounds == 1, (ok, rounds)
    assert len(calls) == 1, f"收敛即停, 不许再多打: {len(calls)}"

    # ② 真机实际序列: 第 1 遍被钳成 [16,16] → 第 2 遍落 [d,d]
    seq = [["2026-09-16", "2026-09-16"], [date, date]]
    fails = []
    calls2 = []
    ok2, rounds2, vals2 = ad._bill_retry_typing(
        lambda: calls2.append(1), lambda: seq[min(len(calls2) - 1, 1)], date,
        on_fail=lambda i, v: fails.append((i, list(v))))
    assert ok2 is True and rounds2 == 2, (ok2, rounds2)
    assert calls2 == [1, 1], f"应当恰好打 2 遍: {calls2}"
    assert fails == [(1, ["2026-09-16", "2026-09-16"])], f"第1遍失败要留证: {fails}"
    print("✅ 控制流: ①第1遍即成只打1遍 ②真机序列 [[16,16],[d,d]] 第2遍成(第1遍留证)")

    # ③ 3 遍全败 → False(交给日历兜底)
    ad3 = MeituanAdapter.__new__(MeituanAdapter)
    n = []
    ok3, rounds3, vals3 = ad3._bill_retry_typing(
        lambda: n.append(1), lambda: ["2026-09-16", "2026-09-16"], date)
    assert ok3 is False and rounds3 == 3 and len(n) == 3, (ok3, rounds3, len(n))
    assert vals3 == ["2026-09-16", "2026-09-16"], vals3

    # ④ 键入自身抛异常 → 照样计一遍并继续(不许把异常冒到调用方)
    boom = {"n": 0}

    def _raise():
        boom["n"] += 1
        raise RuntimeError("click boom")
    ok4, rounds4, _ = ad3._bill_retry_typing(_raise, lambda: [date, date], date)
    assert ok4 is False and rounds4 == 3 and boom["n"] == 3, (ok4, rounds4, boom)
    print("✅ 控制流: ③3遍全败→False ④键入抛异常不冒出(仍跑满3遍)")


def test_bill_type_dates_three_rounds():
    """接线: `_type_bill_dates` 的 max_rounds 默认 3, 且每遍失败都写一行 `🔁 ...第i/3遍` 日志。"""
    ad = MeituanAdapter.__new__(MeituanAdapter)
    logs = []
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append(m)})()
    reads = iter([["2026-09-16", "2026-09-16"], ["2026-09-16", "2026-09-16"],
                  ["2026-09-13", "2026-09-13"]])
    ad._bill_read = lambda page: next(reads, ["", ""])
    typed = []
    # 用一张"惰性页"替代真页: 单遍键入体只要求 _bill_date_inputs 可调用(mouse/keyboard 由假件吞)
    src_ok, rounds, _ = ad._bill_retry_typing(
        lambda: typed.append(1), lambda: ad._bill_read(None), "2026-09-13",
        on_fail=lambda i, v: logs.append(f"🔁 对账页日期第{i}/3遍: 读回={v}"))
    assert src_ok is True and rounds == 3, (src_ok, rounds)
    assert sum(1 for m in logs if "第" in m and "/3遍" in m) == 2, \
        f"前两遍失败各要一行日志: {logs}"
    print("✅ 接线: 3 遍循环, 每遍失败一行 `🔁 ...第i/3遍` 日志")


def test_out_of_window_skips_fast():
    """窗外行为(真机 9-13/9-14 那一态): 秒级返回 False —— **不翻月/不点击/不键入/不写重取证包**,
    只留**一行** warn 说清「客观取不到」。"""
    ad = MeituanAdapter.__new__(MeituanAdapter)
    logs = []
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append((level, m))})()
    ad._flow_today = lambda: datetime.date(2026, 10, 15)      # 钉住"今天"= 真机那一天

    calls = {"input": 0, "click": 0, "type": 0, "nav": 0, "pack": 0}

    class P:
        class mouse:
            @staticmethod
            def click(*a, **k):
                calls["click"] += 1

        class keyboard:
            @staticmethod
            def type(*a, **k):
                calls["type"] += 1

            @staticmethod
            def press(*a, **k):
                calls["type"] += 1
    ad._flow_inputs = lambda page: (calls.__setitem__("input", calls["input"] + 1), [])[1]
    ad._nav_month_roo = lambda page, d: (calls.__setitem__("nav", calls["nav"] + 1), True)[1]
    ad._date_pack = lambda *a, **k: (calls.__setitem__("pack", calls["pack"] + 1), "")[1]

    p = P()
    assert ad._set_flow_date(p, "2026-09-13") is False, "窗外日期必须 False(宁缺勿假)"
    assert calls == {"input": 0, "click": 0, "type": 0, "nav": 0, "pack": 0}, \
        f"窗外必须秒退: 不许探测/点击/键入/翻月/写取证包: {calls}"
    warns = [(lv, m) for lv, m in logs if lv == "warn"]
    assert len(logs) == 1 and len(warns) == 1, f"窗外只许一行日志: {logs}"
    msg = logs[0][1]
    assert "2026-09-13" in msg and "超出美团可取窗口" in msg and "9-15" in msg and "10-14" in msg, msg
    assert "商圈TOP10 留空" in msg and "宁缺勿假" in msg, msg
    print("✅ 窗外: 秒级 False + 恰好一行 warn(含窗口区间/留空/宁缺勿假) + 零探测零点击零取证包")


def main():
    test_flow_date_reachable_fixed_today()
    test_flow_date_reachable_bounds()
    test_flow_date_reachable_robust()
    test_out_of_window_skips_fast()
    test_bill_typing_converged()
    test_bill_retry_typing_control_flow()
    test_bill_type_dates_three_rounds()
    print("✅ 任务 M4 纯逻辑(流量页窗口判定 + 对账页3遍收敛)单测全部通过")


if __name__ == "__main__":
    main()
