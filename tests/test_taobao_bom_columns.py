# -*- coding: utf-8 -*-
"""淘宝爆品团流量列单测: 整体 + 新客/老客 都必须写进模板(2026-09-28 补新客老客)。

真机背景: 爆品团报表 43 列里本来就有 进店人数/进店转化率/下单人数/下单转化率 及
新客/老客分列, 但映射只写了「整体」5 列, 新客/老客 6 列一直是空的(用户报"爆品团
的进店和转化数据没有")。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms.taobao_mapping import TAOBAO_MAPPING, build_write_set   # noqa: E402

# 真机 2026-09-27 爆品团报表那行的值
BOM = {"曝光人数": 29164.0, "进店人数": 687.0, "进店转化率": 0.401,
       "下单人数": 209.0, "下单转化率": 4.753,
       "新客曝光人数": 26330.0, "新客进店人数": 439.0, "新客进店转化率": 0.29,
       "老客曝光人数": 2834.0, "老客进店人数": 248.0, "老客进店转化率": 1.466}

EXPECT = {
    # 整体(原有)
    "EZ": 29164.0, "FC": 687.0, "FF": 0.401, "FI": 209.0, "FL": 4.753,
    # 新客/老客(2026-09-28 补)
    "FO": 26330.0, "FR": 439.0, "FU": 0.29,
    "FX": 2834.0, "GA": 248.0, "GD": 1.466,
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
    assert vals["FO"] != vals["EZ"] and vals["GA"] != vals["FC"], "新客/老客 疑似误取整体列"
    print("✅ 分列校验: 新客/老客值确实来自报表的分客列(≠整体值)")

    # 报表缺这些字段时 → 留空, 不能臆造
    vals2 = build_write_set({}, {"进店人数": 5.0}, capture=None)
    assert vals2.get("FO") is None and vals2.get("GA") is None, vals2.get("FO")
    print("✅ 缺字段: 留空(不臆造)")

    print("✅ 淘宝爆品团流量列测试全部通过")


if __name__ == "__main__":
    main()
