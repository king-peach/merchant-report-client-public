# -*- coding: utf-8 -*-
"""管家区字母验收(任务 J·A 修订, 2026-10-15): 用真模板核对表头列位 + 字母集一致性。

真机背景(2026-10-14 gj 日志原文): `区间 … 共1天 × 17 店 (美团管家 HO~HT)` —— 代码在用
**旧版**字母(HO 起), 而客户用的是 10.13 模板(管家区从 **HN** 起, 整体左移一格)。
后果: 整块右移一格 —— `HN`(店内营业额)永远空, 客户模板公式 `IH=E+DG+HH+HN` 因此少算。

本文件的两层验收:
  ① **真模板表头逐列核对**: 10.13 → HN 起;
  ③ **字母一致性**: gj_mapping 用的字母必须是 10.13 那套(防退回旧版字母)。

旧版模板的「自动平移兜底」已随「模板结构自动识别」一并删除(2026-10-15, 用户口径:
一律按 10.13 列位直写, 用错模板会**直接错列**) —— 本文件不再验收旧版落点
(历史见 git log: 96df5ff → b5df4ee)。

真模板不在(公开仓/CI)→ 跳过, 不失败, 与 test_formula_col_protection 同策略。
"""
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
import openpyxl                                                              # noqa: E402
from merchant_report import fill as F                                        # noqa: E402
from merchant_report.platforms import gj_mapping as gm                       # noqa: E402

NEW_TPL = pathlib.Path("/Users/demo/.hermes/cache/scratch/mrc-fix-1013/refs/运营报表模板10.13.xlsx")
DATE = "2026-09-12"

# 真机 2026-10-06 绿地之窗渠道读数(与 test_gj_overview_parse / test_gj_ia_ib_id_ie 同源)
PARSED = {
    "店内销售": {"营业额": 277.0, "营业收入": 167.30, "订单量": 14.0},
    "自提销售": {"营业额": 88.0, "营业收入": 60.0, "订单量": 6.0},
    "自营外卖": {"营业额": 100.0, "营业收入": 80.0, "订单量": 3.0},
    "_sensitive": {"times": 6.0, "amt": 131.4},
}
# **10.13 列位**下 build_write_set 的期望产物(逐列):
#   店内销售 277/167.3/14 · 自提 88/60/6 · 抖音合计(=自营外卖, 后台无抖音行) 100/80/3
#   自营外卖 100/80/3 · 敏感 HZ=6/IA=131.4 · 工时 IB=0(用户口径) · 单量 线上9/线下14/合计23
NEW_EXPECT = {"HN": 277.0, "HO": 167.3, "HP": 14.0,
              "HQ": 88.0, "HR": 60.0, "HS": 6.0,
              "HT": 100.0, "HU": 80.0, "HV": 3.0,
              "HW": 100.0, "HX": 80.0, "HY": 3.0,
              "HZ": 6.0, "IA": 131.4, "IB": 0,
              "IC": 9.0, "ID": 14.0, "IE": 23.0}


def _row5(ws, cols):
    return {c: ws[f"{c}5"].value for c in cols}


def test_real_template_headers():
    """① 10.13 真模板表头逐列核对（HN 起）。"""
    if not NEW_TPL.exists():
        print("… 10.13 真模板不在, 跳过真模板表头核对")
        return False
    cols = ("HM", "HN", "HO", "HP", "HQ", "HR", "HS", "HT", "HU",
            "HV", "HW", "HX", "HY", "HZ", "IA", "IB", "IC", "ID", "IE", "IF")
    wb = openpyxl.load_workbook(NEW_TPL, read_only=True)     # 17MB 表: read_only 秒开
    try:
        ws = wb["铁道"]
        n = _row5(ws, cols)
    finally:
        wb.close()
    # 10.13: HN=店内销售营业额 … HY=自营外卖单量 / HZ 敏感次数 / IA 敏感金额 / IB 工时 / IC~IE 单量
    assert n["HN"] == "营业额-\n店内销售", n["HN"]
    assert n["HY"] == "自营外卖单量", n["HY"]
    assert (n["HZ"], n["IA"], n["IB"]) == ("敏感操作次数", "敏感操作金额", "当日工时"), \
        (n["HZ"], n["IA"], n["IB"])
    assert (n["IC"], n["ID"], n["IE"]) == ("线上单量", "线下单量", "单量合计"), \
        (n["IC"], n["ID"], n["IE"])
    print("✅ ① 真模板表头: 10.13 从 HN 起, HN/HY/HZ~IE 逐列核对通过"
          "（HN=「营业额-店内销售」）")
    return True


def test_letters_are_1013_set():
    """③ 字母一致性: gj 用的必须是 10.13 那套(防退回旧版字母)。"""
    cols = [c for c, _cat, _f in gm.GJ_MAPPING]
    assert cols == ["HN", "HO", "HP", "HQ", "HR", "HS", "HT", "HU", "HV", "HW", "HX", "HY"], cols
    w = gm.build_write_set(PARSED)
    for c in ("HZ", "IA", "IB", "IC", "ID", "IE"):
        assert c in w, f"{c} 必须由 build_write_set 产出(旧版字母 IA/IB/IC/ID/IE/IF 已废)"
    assert "IF" not in w, w                   # 旧版把单量合计写在 IF; 现在必须 IE
    bad = {c: (w.get(c), v) for c, v in NEW_EXPECT.items() if w.get(c) != v}
    assert not bad, f"10.13 列位写集不符: {bad}"
    print(f"✅ ③ gj 字母 = 10.13 那套({len(NEW_EXPECT)} 列逐列核对: HN~HY + HZ/IA/IB/IC/ID/IE)")


def main():
    test_real_template_headers()
    test_letters_are_1013_set()
    print("\n✅ 管家区字母(10.13 真模板表头 + 字母集一致性)验收通过")


if __name__ == "__main__":
    main()
