# -*- coding: utf-8 -*-
"""京东「一店一 Sheet」解析单测: 逐店行 vs 聚合行必须一致。

京东报表天然是「每店每天一行」(_parse_jd_rows 保留逐店行供一店一 Sheet),
老的聚合版(_parse_jd_file)把同一天多家店加成一个数写主表。
两者必须来自同一份数据 → 聚合 == 逐店加总, 否则主表与门店 Sheet 会对不上。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kernel"))

import openpyxl                                                     # noqa: E402
from merchant_report.platforms.jd import JDAdapter                   # noqa: E402

FAILS = []
HDR = ["门店名称", "门店id", "日期", "城市", "收入", "营业额", "支出", "有效订单", "曝光人数", "入店人数"]
DATA = [
    ["A店(长沙金盆岭店)", 1, "2026-09-20", "长沙", 100.5, 200.0, 50.0, 10, 500, 20],
    ["B店(长沙北辰店)", 2, "2026-09-20", "长沙", 200.25, 400.0, 80.0, 20, 800, 30],
    ["A店(长沙金盆岭店)", 1, "2026-09-19", "长沙", 111.0, 222.0, 60.0, 11, 511, 21],
    ["B店(长沙北辰店)", 2, "2026-09-19", "长沙", 222.0, 444.0, 90.0, 22, 822, 33],
]


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def make_file():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(HDR)
    for row in DATA:
        ws.append(row)
    p = os.path.join(tempfile.mkdtemp(), "京东_门店_20260919_20260920.xlsx")
    wb.save(p)
    return p


def main():
    p = make_file()

    print("【1】逐店行: 按日期分组且保留门店名")
    per = JDAdapter._parse_jd_rows(p)
    check("两个日期", sorted(per) == ["2026-09-19", "2026-09-20"], str(sorted(per)))
    check("每天 2 家店", all(len(v) == 2 for v in per.values()), str({k: len(v) for k, v in per.items()}))
    check("门店名保留完整", any("金盆岭" in str(r.get("门店名称")) for r in per["2026-09-20"]),
          str([r.get("门店名称") for r in per["2026-09-20"]]))

    print("【2】聚合行: 同日期数值列求和, 文本列取首个非空")
    agg = JDAdapter._parse_jd_file(p)
    d = agg["2026-09-20"]
    check("收入=100.5+200.25", abs(float(d["收入"]) - 300.75) < 1e-6, str(d.get("收入")))
    check("营业额=200+400", abs(float(d["营业额"]) - 600) < 1e-6, str(d.get("营业额")))
    check("有效订单=10+20", abs(float(d["有效订单"]) - 30) < 1e-6, str(d.get("有效订单")))
    check("文本列取首个非空", d.get("日期") == "2026-09-20" or str(d.get("日期"))[:10] == "2026-09-20",
          str(d.get("日期")))

    print("【3】两者必须一致(主表 == 逐店加总, 对不上就是写入错)")
    for day in ("2026-09-19", "2026-09-20"):
        rows = per[day]
        sums = {c: sum(float(r[c]) for r in rows) for c in ("收入", "营业额", "有效订单")}
        a = agg[day]
        same = all(abs(sums[c] - float(a[c])) < 1e-6 for c in sums)
        check(f"{day} 聚合 == 逐店加总", same, f"agg={ {c: a[c] for c in sums} } sums={sums}")

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
