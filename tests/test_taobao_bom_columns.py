# -*- coding: utf-8 -*-
"""淘宝爆品团流量列单测: 整体 + 新客/老客 都必须写进模板(2026-09-28 补新客老客)。

真机背景: 爆品团报表 43 列里本来就有 进店人数/进店转化率/下单人数/下单转化率 及
新客/老客分列, 但映射只写了「整体」5 列, 新客/老客 6 列一直是空的(用户报"爆品团
的进店和转化数据没有")。

列位(2026-10-13 修复后模板, 旧列位全部 -1): 整体 EY/FB/FE/FH/FK、
新客 FN/FQ/FT、老客 FW/FZ/GC —— 且新客/老客第 3 列列名是「下单转化率」(不是进店转化率)。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms.taobao_mapping import TAOBAO_MAPPING, build_write_set   # noqa: E402

# 真机 2026-09-27 爆品团报表那行的值
BOM = {"曝光人数": 29164.0, "进店人数": 687.0, "进店转化率": 0.401,
       "下单人数": 209.0, "下单转化率": 4.753,
       "新客曝光人数": 26330.0, "新客进店人数": 439.0, "新客进店转化率": 0.29,
       "新客下单转化率": 32.5,
       "老客曝光人数": 2834.0, "老客进店人数": 248.0, "老客进店转化率": 1.466,
       "老客下单转化率": 41.2}

EXPECT = {
    # 整体(原有, 修复后模板列位 EY/FB/FE/FH/FK)
    "EY": 29164.0, "FB": 687.0, "FE": 0.401, "FH": 209.0, "FK": 4.753,
    # 新客/老客(2026-09-28 补; 修复后模板列位 FN/FQ/FT + FW/FZ/GC)
    # 第 3 列 = **下单转化率**(规格书 §4.7: 旧代码错填了同漏斗上一级的"进店转化率")
    "FN": 26330.0, "FQ": 439.0, "FT": 32.5,
    "FW": 2834.0, "FZ": 248.0, "GC": 41.2,
}


def main():
    m = {col: src for col, src, _ in TAOBAO_MAPPING}
    for col in EXPECT:
        assert col in m, f"映射里缺 {col}"
        assert m[col].startswith("BOM:"), f"{col} 应取爆品团报表, 实际 {m[col]}"
    print(f"✅ 映射表: 爆品团流量 {len(EXPECT)} 列全部指向 BOM(爆品团报表)")

    vals = build_write_set({}, BOM, capture=None)
    bad = {c: (vals.get(c), v) for c, v in EXPECT.items() if vals.get(c) != v}
    assert not bad, f"写入值不对: {bad}"
    print("✅ 写入值: " + "  ".join(f"{c}={vals[c]}" for c in EXPECT))

    # 新客/老客必须取分列, 不能等于整体(否则就是取错列)
    assert vals["FN"] != vals["EY"] and vals["FZ"] != vals["FB"], "新客/老客 疑似误取整体列"
    print("✅ 分列校验: 新客/老客值确实来自报表的分客列(≠整体值)")

    # 报表缺这些字段时 → 留空, 不能臆造
    vals2 = build_write_set({}, {"进店人数": 5.0}, capture=None)
    assert vals2.get("FN") is None and vals2.get("FZ") is None, vals2.get("FN")
    print("✅ 缺字段: 留空(不臆造)")

    # 缺「下单转化率」直列时 → 用 下单人数/进店人数 自算(不拿进店转化率顶替)
    vals3 = build_write_set({}, {"新客曝光人数": 10, "新客进店人数": 50, "新客下单人数": 10,
                                 "老客曝光人数": 10, "老客进店人数": 20, "老客下单人数": 5},
                            capture=None)
    assert vals3["FT"] == round(10 / 50, 6) and vals3["GC"] == round(5 / 20, 6), (vals3["FT"], vals3["GC"])
    print("✅ 缺率值直列: 新客/老客下单转化率按 下单/进店 自算(不退回进店转化率)")

    print("✅ 淘宝爆品团流量列测试全部通过")


if __name__ == "__main__":
    main()
