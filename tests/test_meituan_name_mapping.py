# -*- coding: utf-8 -*-
"""美团报表: 按**表头名**取值(抗平台改列序/列数) + 名字对不上就**不写**(不猜字母位置)。

背景(2026-10-01 真机审计): 平台会按指标集给列(同一商家 82/83 列都出现过), 旧实现按"列字母位置"
取值 → 全部业务表 13 列、流量区 6 列取到隔壁列的值(如 G"总营业收入" ← Q 有效订单=75) = 静默错数据。
"""
import csv
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan_mapping as mm                    # noqa: E402

H = ["日期", "门店名称", "门店id", "营业收入", "有效订单", "曝光人数", "入店人数", "入店转化率",
     "下单转化率", "下单人数", "曝光新客", "入店新客", "新客入店转化率", "曝光老客", "入店老客",
     "老客入店转化率", "服务负反馈率指标得分", "食品安全负反馈率指标得分", "食品安全负反馈率"]
V = ["2026-09-28", "测试门店", "1", "845.84", "75", "2545", "160", "0.0629", "0.4375", "70",
     "1969", "86", "0.0437", "576", "74", "0.1285", "5.0", "4.3", "6.11"]


def write(d, name, headers, values):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(headers)
        w.writerow(values)
    return p


def build(d):
    rows, bao, _ = mm.collect(d, "2026-09-28", "2026-09-28")
    r = rows["2026-09-28"][0]
    mm.NAME_FALLBACKS.clear()
    return r, mm.build_write_set(r, None, None)


def main():
    # ① 常规列序: 按名取值 = 报表原值
    d1 = tempfile.mkdtemp()
    write(d1, "美团_全部业务_20260928_20260928.csv", H, V)
    r, ws = build(d1)
    assert ws["L"] == 2545.0, ws["L"]          # 曝光人数
    assert ws["O"] == 160.0, ws["O"]           # 入店人数
    assert ws["U"] == 70.0, ws["U"]            # 下单人数
    assert ws["X"] == 0.4375, ws["X"]          # 下单转化率
    assert ws["AA"] == 1969.0, ws["AA"]        # 曝光新客
    assert ws["AM"] == 74.0, ws["AM"]          # 入店老客
    assert ws["CV"] == 5.0 and ws["CW"] == 4.3 and ws["CX"] == 6.11, (ws["CV"], ws["CW"], ws["CX"])
    print("✅ 常规列序: 流量区 + 负反馈三列 按名取到正确值")

    # ② 打乱列顺序(平台改列序): 结果必须**完全一致** —— 这是这次修复的核心
    order = list(range(1, len(H)))             # 除日期外全部打乱
    order = order[::-1]
    H2 = [H[0]] + [H[i] for i in order]
    V2 = [V[0]] + [V[i] for i in order]
    d2 = tempfile.mkdtemp()
    write(d2, "美团_全部业务_20260928_20260928.csv", H2, V2)
    r2, ws2 = build(d2)
    assert ws2["L"] == 2545.0 and ws2["O"] == 160.0 and ws2["U"] == 70.0, \
        f"列序打乱后取错值: L={ws2['L']} O={ws2['O']} U={ws2['U']}"
    assert ws2["CX"] == 6.11 and ws2["CV"] == 5.0, (ws2["CX"], ws2["CV"])
    print("✅ 列序完全打乱: 取到的值仍然一致(证明不再依赖列位置)")

    # ③ 名字对不上 → **不写**(返回 None), 不按字母位置猜
    H3 = [h for h in H if h != "食品安全负反馈率"]
    V3 = [v for h, v in zip(H, V) if h != "食品安全负反馈率"]
    d3 = tempfile.mkdtemp()
    write(d3, "美团_全部业务_20260928_20260928.csv", H3, V3)
    r3, ws3 = build(d3)
    assert ws3["CX"] is None, f"表头名缺失时必须留空, 不得按位置猜: {ws3['CX']}"
    # 非 "=" 条目在名字对不上时会记录待人工核对(店铺分 在全部业务报表里存在, 但本测试的样例表里没有)
    assert any("店铺分" in x for x in mm.NAME_FALLBACKS), mm.NAME_FALLBACKS[:6]
    print(f"✅ 表头名缺失 → 留空并记录({len(set(mm.NAME_FALLBACKS))} 列待人工核对), 不猜位置")

    print("✅ 美团按表头名取值(抗列序/列数变化)单测全部通过")


if __name__ == "__main__":
    main()
