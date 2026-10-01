# -*- coding: utf-8 -*-
"""单门店模式 Sheet 选择单测(2026-09-28 用户口径: 单门店**更要**找关键词 sheet, 不能写主表)。

匹配顺序: 关键词/门店名 → match_sheet(精确/关键词包含/反向/配置关键词) → 命中即用;
都没命中 → 按地名关键字返回**新建 Sheet 名**(fill_many 会复刻第 3 个 sheet 建出来)。
硬约束: 返回值**永远不能是 Sheet2/Sheet3 这类主表名**。
"""
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
import openpyxl                                                              # noqa: E402
from merchant_report.orchestrator import _single_store_sheet                  # noqa: E402

MAINS = ("Sheet2", "Sheet3")


def mk(path, sheets):
    wb = openpyxl.Workbook()
    wb.active.title = sheets[0]
    for s in sheets[1:]:
        wb.create_sheet(s)
    wb.save(path)
    return path


def main():
    D = tempfile.mkdtemp(prefix="one_store_")
    tpl = mk(D + "/t.xlsx", ["Sheet2", "Sheet3", "岚霞路", "金盆岭", "北辰梅尼", "万家丽广场一楼"])
    cfg = {}

    cases = {
        "金盆岭": "金盆岭",              # 精确
        "长沙金盆岭理工大学店": "金盆岭",     # 关键词包含
        "北辰": "北辰梅尼",              # 反向包含
        "万家丽广场一楼店": "万家丽广场一楼",
        "长沙麻园湾店": "麻园湾",           # 模板里没有 → 抽地名做新 Sheet 名
        "不存在的某某店": "不存在的某某",
    }
    for kw, want in cases.items():
        got = _single_store_sheet({"keyword": kw}, cfg, tpl)
        assert got == want, f"keyword={kw!r}: 期望 {want!r}, 实际 {got!r}"
        assert got not in MAINS, f"❌ 绝不能写主表({got!r})"
        print(f"✅ keyword={kw!r:18s} → Sheet「{got}」")

    # 关键词为空 → 退回门店名
    got = _single_store_sheet({"name": "长沙高桥店"}, cfg, tpl)
    assert got == "高桥", got
    print(f"✅ 关键词为空 → 用门店名解析: 「{got}」")

    # 模板读不到(路径错) → 不能崩, 也不能返回主表名
    got = _single_store_sheet({"keyword": "某某店"}, cfg, D + "/不存在.xlsx")
    assert got and got not in MAINS, got
    print(f"✅ 模板不可读: 不崩且不回落主表(返回「{got}」)")

    # 全空 → 有兜底名, 仍不能是主表
    got = _single_store_sheet({}, cfg, tpl)
    assert got and got not in MAINS, got
    print(f"✅ 全空输入: 兜底「{got}」, 仍不是主表")

    shutil.rmtree(D, ignore_errors=True)
    print("✅ 单门店 Sheet 选择测试全部通过(永不写主表)")


if __name__ == "__main__":
    main()
