# -*- coding: utf-8 -*-
"""推广账户流水解析: CJ 充值金额 / CE 推广消耗 / 账户余额。

真机取样(2026-10-08, 单店 高桥 55555555, 页面 /ad/v1/pc#/account?scrollConsume=true):
  时间 | 类型 | 变化金额(元) | 余额 | 操作
  2026-10-08 00:00:00~10:56:18  推广消费        -85.92 预扣款  109.09
  2026-10-07 00:00:00~23:59:59  推广消费        -237.16        109.09
  2026-10-07 14:54:46           推广账户自动充值 +200.00        346.25
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan as mt                          # noqa: E402

ROWS = [
    ["2026-10-08 00:00:00~10:56:18", "推广消费", "-85.92 预扣款", "109.09", "详情"],
    ["2026-10-07 00:00:00~23:59:59", "推广消费", "-237.16", "109.09", "详情"],
    ["2026-10-07 14:54:46", "推广账户自动充值", "+200.00", "346.25", "详情"],
    ["2026-10-06 00:00:00~23:59:59", "推广消费", "-250.00", "146.25", "详情"],
    ["2026-10-06 20:51:05", "推广账户自动充值", "+200.00", "396.25", "详情"],
]


def main():
    P = mt.MeituanAdapter.parse_ledger

    # ① 10-07: 一笔充值 200 + 一笔消费 237.16(取绝对值), 余额取该日最近一条
    r = P(ROWS, "2026-10-07")
    assert r["recharge"] == 200.0, r
    assert r["consume"] == 237.16, r
    assert r["balance"] == 109.09, r          # 表格倒序 → 该日第一条的余额
    assert (r["n_recharge"], r["n_consume"]) == (1, 1), r
    print(f"✅ 2026-10-07: 充值 {r['recharge']} / 消耗 {r['consume']} / 余额 {r['balance']}")

    # ② 10-08(当天): 消费带"预扣款"后缀也要能解析
    r = P(ROWS, "2026-10-08")
    assert r["consume"] == 85.92, r
    assert r["recharge"] is None, f"当天没有充值 → 应为 None(不是 0): {r}"
    print("✅ 2026-10-08: 解析出 -85.92(剥掉'预扣款'), 无充值 → None")

    # ③ 同一天多笔充值 → 求和
    r = P(ROWS + [["2026-10-07 09:00:00", "推广账户自动充值", "+100.50", "50.00", "详情"]], "2026-10-07")
    assert r["recharge"] == 300.5 and r["n_recharge"] == 2, r
    print(f"✅ 同日多笔充值 → 合计 {r['recharge']}({r['n_recharge']} 笔)")

    # ④ 没有该日期的行 → 全是 None(**不臆造 0**: 0 是"确实没充值"的真实值, 读不到是另一回事)
    r = P(ROWS, "2026-09-30")
    assert r["recharge"] is None and r["consume"] is None and r["balance"] is None, r
    print("✅ 无该日数据 → 全 None(绝不臆造 0)")

    # ⑤ 脏数据不崩(空行/列不足)
    assert P([[], ["2026-10-07"], None], "2026-10-07")["recharge"] is None
    print("✅ 脏行/空行不崩")

    print("✅ 推广账户流水(CJ 充值 / CE 消耗 / 余额)解析单测全部通过")


if __name__ == "__main__":
    main()
