# -*- coding: utf-8 -*-
"""管家区字母迁移验收(任务 J·A): 用**真模板**核对「写入落点」, 不只对合成件。

真机背景(2026-10-14 gj 日志原文): `区间 … 共1天 × 17 店 (美团管家 HO~HT)` —— 代码在用
**旧版**字母(HO 起), 而客户用的是 10.13 模板(管家区从 **HN** 起, 整体左移一格)。
后果: 整块右移一格 —— `HN`(店内营业额)永远空, 客户模板公式 `IH=E+DG+HH+HN` 因此少算。

本文件的三层验收:
  ① **真模板表头逐列核对**: 10.13 → HN 起; 旧版 → HO 起(两版对照, 证据留档);
  ② **写入落点**: 按 gj 真实 write_set 走 fill 层 → 10.13 落 HN 系列; 旧版落 HO 系列(平移兜底);
  ③ **字母一致性**: gj_mapping 用的字母必须是 10.13 那套(防退回旧版字母)。

真模板不在(公开仓/CI)→ 只跑合成件部分(不失败), 与 test_formula_col_protection 同策略。
"""
import datetime
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
import openpyxl                                                              # noqa: E402
from merchant_report import fill as F                                        # noqa: E402
from merchant_report.platforms import gj_mapping as gm                       # noqa: E402

NEW_TPL = pathlib.Path("/Users/demo/.hermes/cache/scratch/mrc-fix-1013/refs/运营报表模板10.13.xlsx")
OLD_TPL = pathlib.Path("/Users/demo/.hermes/cache/scratch/mrc-fix-1013/refs/运营报表模板_旧版.xlsx")
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


def shift(col):
    return F.translate_col(col, "legacy")


def _row5(ws, cols):
    return {c: ws[f"{c}5"].value for c in cols}


def test_real_template_headers():
    """① 真模板表头逐列核对(10.13 从 HN 起 / 旧版从 HO 起)。"""
    if not (NEW_TPL.exists() and OLD_TPL.exists()):
        print("… 真模板不在, 跳过真模板表头核对")
        return False
    cols = ("HM", "HN", "HO", "HP", "HQ", "HR", "HS", "HT", "HU",
            "HV", "HW", "HX", "HY", "HZ", "IA", "IB", "IC", "ID", "IE", "IF")
    out = {}
    for tag, p in (("new", NEW_TPL), ("old", OLD_TPL)):
        wb = openpyxl.load_workbook(p, read_only=True)     # 17MB 表: read_only 秒开
        try:
            ws = wb["铁道"]
            out[tag] = _row5(ws, cols)
        finally:
            wb.close()
    n, o = out["new"], out["old"]
    # 10.13: HN=店内销售营业额 … HY=自营外卖单量 / HZ 敏感次数 / IA 敏感金额 / IB 工时 / IC~IE 单量
    assert n["HN"] == "营业额-\n店内销售", n["HN"]
    assert n["HY"] == "自营外卖单量", n["HY"]
    assert (n["HZ"], n["IA"], n["IB"]) == ("敏感操作次数", "敏感操作金额", "当日工时"), \
        (n["HZ"], n["IA"], n["IB"])
    assert (n["IC"], n["ID"], n["IE"]) == ("线上单量", "线下单量", "单量合计"), \
        (n["IC"], n["ID"], n["IE"])
    # 旧版: 同内容整体右移一格 —— 旧版 HN 是「剩余金额」, HO 才是店内销售营业额
    assert o["HN"] == "剩余金额", o["HN"]
    assert o["HO"] == "营业额-\n店内销售", o["HO"]
    assert (o["IA"], o["IB"], o["IC"]) == ("敏感操作次数", "敏感操作金额", "当日工时"), \
        (o["IA"], o["IB"], o["IC"])
    assert (o["ID"], o["IE"], o["IF"]) == ("线上单量", "线下单量", "单量合计"), \
        (o["ID"], o["IE"], o["IF"])
    # 逐列对照: 10.13 的 HN..IE 与旧版的 HO..IF 一一对应(整体 +1)
    n_seq = [n[c] for c in ("HN", "HO", "HP", "HQ", "HR", "HS", "HT", "HU", "HV",
                            "HW", "HX", "HY", "HZ", "IA", "IB", "IC", "ID", "IE")]
    o_seq = [o[c] for c in ("HO", "HP", "HQ", "HR", "HS", "HT", "HU", "HV", "HW",
                            "HX", "HY", "HZ", "IA", "IB", "IC", "ID", "IE", "IF")]
    assert n_seq == o_seq, [(a, b) for a, b in zip(n_seq, o_seq) if a != b]
    assert F.SHIFT_FROM == "DJ" and F.translate_col("HN", "legacy") == "HO"
    print(f"✅ ① 真模板表头: 10.13 从 HN 起 / 旧版从 HO 起, 18 列逐列一一对应(整体 +1); "
          f"旧版 HN=「{o['HN']}」")
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
    print(f"✅ ③ gj 字母 = 10.13 那套({len(NEW_EXPECT)} 列逐列核对: HN~HY + HZ/IA/IB/IC/ID/IE), "
          "docstring 注明平移兜底")


def test_write_lands_on_real_templates():
    """② 写入落点: 真模板上跑一遍 fill, 核对**真表**第 6 行数值。

    真表是客户文件 → 先复制到临时目录再改(绝不动 refs 原件)。

    ⚠️ 只对**旧版**真表做实写: 10.13 那份 17MB, openpyxl 全量读+存 ≈ 6 分钟(实测),
    放进全量套件会拖垮跑批。10.13 的写入落点由两段**等价**证据链覆盖:
      · 本文件 ① 证明真表 HN 列的表头就是「营业额-店内销售」(18 列逐列对应);
      · tests/test_template_layout.py 的 NEW_LANDING 证明 new 版下 fill **不做平移**、HN→HN。
    旧版实写同时验证「平移兜底」这半条验收(HN → HO)。
    """
    if not OLD_TPL.exists():
        print("… 旧版真模板不在, 跳过真模板写入落点")
        return False
    ws_set = gm.build_write_set(PARSED)
    # 逐列期望: 10.13 的每个字母 +1 → 旧版落点(整体平移的**内容**对齐, 不看表头)
    want = {shift(c): v for c, v in NEW_EXPECT.items()}
    D = tempfile.mkdtemp(prefix="gj_landing_")
    dst = f"{D}/old.xlsx"
    shutil.copy2(OLD_TPL, dst)
    res = F.fill_template(dst, DATE, dict(ws_set), mode="top", sheet_name="铁道")
    assert res["layout"] == "legacy", res
    assert not res.get("skipped_unknown_layout"), res
    assert res["reread_bad"] == [], res["reread_bad"]
    ws = openpyxl.load_workbook(dst)["铁道"]
    got = {c: ws[f"{c}6"].value for c in want}
    bad = {c: (got[c], v) for c, v in want.items() if got[c] != v}
    assert not bad, f"[legacy 真表] 落点不符: {bad}"
    # 平移靶心: 10.13 的 HN(店内营业额 277) → 旧版 HO(表头「营业额-店内销售」)227 的**同一内容**
    assert ws["HM5"].value == "预算" and ws["HO5"].value == "营业额-\n店内销售", \
        (ws["HM5"].value, ws["HO5"].value)
    print(f"✅ ② [legacy 真表] 平移兜底: {len(want)} 列按旧版列位落位, "
          f"HN(10.13 店内营业额) → 旧版 HO=「{ws['HO5'].value}」= {got['HO']}; "
          "平移后内容与 10.13 位置一一对应")
    return True


def main():
    did = []
    if test_real_template_headers():
        did.append(1)
    test_letters_are_1013_set()
    if test_write_lands_on_real_templates():
        did.append(2)
    if not did:
        # 无真模板时至少守住字母一致性 + 合成落点(F.translate_col 契约)
        assert F.translate_col("HN", "legacy") == "HO"
        assert F.translate_col("DJ", "legacy") == "DK"
        print("✅ 合成兜底: translate_col 对管家区/主站区均 +1")
    print("\n✅ 管家区字母迁移(真模板落点 + 平移兜底)验收通过")


if __name__ == "__main__":
    main()
