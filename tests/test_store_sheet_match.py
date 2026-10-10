# -*- coding: utf-8 -*-
"""多门店「后台门店名 ↔ 模板 Sheet」关键词匹配单测。

用户口径: 模板一店一 Sheet, Sheet 名=地名关键词(北辰/铁道学院/高桥);
后台门店全名可能不同但**包含**关键词(如「长沙北辰梅尼超市店」含「北辰」);
匹配不到就不写入(不报错、不自动建 Sheet)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kernel"))

from merchant_report import sheet_match as SM

TPL = ["Sheet2", "Sheet3", "北辰", "铁道学院", "高桥"]

print("=== 1. 用户场景: 模板「北辰」↔ 后台「长沙北辰梅尼超市店」(真实数据) ===")
sn, why = SM.match_sheet(TPL, "长沙北辰梅尼超市店")
assert sn == "北辰", (sn, why)
assert "包含" in why
print(f"  ✓ {sn}  ({why})")

print("=== 2. 精确优先 + 最长(最具体)优先 ===")
sn, _ = SM.match_sheet(["北辰", "长沙北辰", "Sheet2"], "长沙北辰梅尼超市店")
assert sn == "长沙北辰", sn          # 两个都能包含命中 → 取更长的
sn, _ = SM.match_sheet(["北辰", "Sheet2"], "北辰")
assert sn == "北辰", sn              # 精确
print("  ✓ 精确>最长包含")

print("=== 3. 主数据表不算门店表 ===")
assert SM.store_sheets(["Sheet1", "Sheet2", "Sheet3", "北辰"]) == ["北辰"]
assert SM.match_sheet(["Sheet2", "Sheet3"], "北辰梅尼超市店")[0] is None, "不许匹配到 Sheet2"
print("  ✓ Sheet2/Sheet3 被排除")

print("=== 4. 归一化: 空格/括号/标点/「店」后缀 ===")
sn, _ = SM.match_sheet(TPL, "长沙·北辰 梅尼超市店")
assert sn == "北辰", sn
sn, _ = SM.match_sheet(["铁道学院店"], "铁道学院(南院)店")
assert sn == "铁道学院店", sn
print("  ✓ 归一化后仍能命中")

print("=== 5. 后台门店对不上模板 → 跳过(不写入) ===")
sn, why = SM.match_sheet(TPL, "金盆岭理工大学店")
assert sn is None and "无对应 Sheet" in why, (sn, why)
print(f"  ✓ 不写入: {why}")

print("=== 6. 模板 Sheet 在后台没有该门店 → idle(保持不写入) ===")
plan = SM.plan_matches(TPL, ["长沙北辰梅尼超市店", "金盆岭理工大学店",
                             "铁道学院店", "高桥大市场店"], ["北辰", "铁道学院", "高桥"])
assert [s for _, s, _ in plan["matched"]] == ["北辰", "铁道学院", "高桥"], plan["matched"]
assert [n for n, _ in plan["unmatched"]] == ["金盆岭理工大学店"], plan["unmatched"]
p2 = SM.plan_matches(TPL, ["铁道学院店"], ["北辰", "铁道学院", "高桥"])
assert p2["idle"] == ["北辰", "高桥"], p2["idle"]
print(f"  ✓ matched={len(plan['matched'])} unmatched={[n for n, _ in plan['unmatched']]} "
      f"idle={p2['idle']}")

print("=== 7. 配置关键词命中(Sheet 名与门店名不含对方全名时) ===")
sn, why = SM.match_sheet(["河西分店表"], "湘潭三大桥河西店", keywords=["河西"])
assert sn == "河西分店表", (sn, why)
print(f"  ✓ {sn} ({why})")

print("=== 8. 模板没有任何门店表 → 不匹配(编排层会给出可执行报错) ===")
plan = SM.plan_matches(["Sheet2", "Sheet3"], ["北辰店"], [])
assert plan["matched"] == [] and plan["candidates"] == [], plan
print("  ✓ 候选为空 → matched 为空")

print("=== 9. 地名关键字抽取(用户口径: 只取地名, 不要全称) ===")
CASES = [
    ("金盆岭理工大学店", "金盆岭"),
    ("万家丽广场一楼店", "万家丽广场一楼"),
    ("长沙万家丽广场七楼店", "万家丽广场七楼"),
    ("湘潭三大桥河西店", "三大桥河西"),
    ("长沙绿地中央广场店", "绿地中央广场"),
    ("XXX-1楼（老店）", "XXX-1楼"),
    ("长沙高桥店", "高桥"),
    ("示例柠檬茶专门店（铁道学院店）", "铁道学院"),
    ("示例柠檬鲜果茶（金盆岭理工大学店）", "金盆岭"),
    ("示例柠檬茶(湘潭三大桥河西店)", "三大桥河西"),
]
for src, want in CASES:
    got = SM.extract_place(src)
    assert got == want, f"{src} → {got} (期望 {want})"
    print(f"  ✓ {src:36s} → 「{got}」")

print("=== 10. 别名/自定义规则可覆盖(便于后续细化) ===")
assert SM.extract_place("金盆岭理工大学店", aliases={"金盆岭理工大学店": "金盆岭店A"}) == "金盆岭店A"
assert SM.extract_place("长沙某某店", city_prefixes=["长沙"], noise_words=["店"]) == "某某"
print("  ✓ aliases / city_prefixes / noise_words 均可配置")

print("=== 11. 没有对应 Sheet → 新建并复刻表头(用户新要求) ===")
import shutil as _sh, tempfile as _tf, openpyxl as _ox
from merchant_report.fill import ensure_sheet
_tpl = Path(_tf.mkdtemp()) / "t.xlsx"
_src = Path("/Users/demo/Downloads/商家报表整理/模板.xlsx")
if _src.exists():
    _sh.copy(_src, _tpl)
    before = _ox.load_workbook(_tpl, read_only=True)
    base_hdr = [before["Sheet2"].cell(row=5, column=c).value for c in range(1, 13)]
    n_before = len(before.sheetnames)
    before.close()
    created = ensure_sheet(str(_tpl), "万家丽广场七楼")
    wb = _ox.load_workbook(_tpl)
    assert created is True, "应新建"
    assert "万家丽广场七楼" in wb.sheetnames, wb.sheetnames
    assert len(wb.sheetnames) == n_before + 1, "只应新增一个表, 不许动别的"
    hdr = [wb["万家丽广场七楼"].cell(row=5, column=c).value for c in range(1, 13)]
    assert hdr == base_hdr, f"表头应为复刻: {hdr[:5]} vs {base_hdr[:5]}"
    wb.close()
    assert ensure_sheet(str(_tpl), "万家丽广场七楼") is False, "已存在时不该重复建"
    print(f"  ✓ 新建「万家丽广场七楼」, 表头逐列一致({len([h for h in hdr if h])} 个非空), 重复调用不重建")
else:
    print("  (跳过: 本机没有真实模板)")

print("\n✅ 门店↔Sheet 关键词匹配单测全部通过")
