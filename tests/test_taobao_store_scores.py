# -*- coding: utf-8 -*-
"""淘宝 店铺评分/满意度/味道/包装 → 模板 GT/GU/GV/GW。

2026-10-08 真机核实: 淘宝门店报表第 98~101 列就是这 4 项(4.5 / 5.0 / 5.0 / 5.0),
数据本来就在报表里, 只是以前没映射 → 模板一直空。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "kernel"))
from merchant_report.platforms import taobao_mapping as tm   # noqa: E402


def main():
    # 用真机报表值构造「全部」行
    all_row = {"店铺评分": "4.5", "满意度得分": "5.0", "味道得分": "5.0", "包装得分": "5.0",
               "无效订单": "0", "投诉订单数": "1"}
    vals = tm.build_write_set(all_row, {}, {})
    got = {c: vals.get(c) for c in ("GT", "GU", "GV", "GW")}
    assert got == {"GT": 4.5, "GU": 5.0, "GV": 5.0, "GW": 5.0}, got
    print(f"① 4 项都映射到模板 ✓  {got}")

    # 缺字段 → 留空(不臆造)
    vals2 = tm.build_write_set({"店铺评分": "4.5"}, {}, {})
    assert vals2.get("GT") == 4.5 and vals2.get("GU") is None, vals2
    print("② 报表缺该字段 → 留空 ✓ (GT=4.5, GU=None)")

    # 映射表里确实有这 4 列
    cols = [c for c, _, _ in tm.TAOBAO_MAPPING] if hasattr(tm, "TAOBAO_MAPPING") else []
    if cols:
        for c in ("GT", "GU", "GV", "GW"):
            assert c in cols, f"{c} 必须在映射表里"
        print(f"③ 映射表含 GT/GU/GV/GW ✓ (共 {len(cols)} 列)")
    print("\n✅ 淘宝店铺评分 4 列 全绿")


if __name__ == "__main__":
    main()
