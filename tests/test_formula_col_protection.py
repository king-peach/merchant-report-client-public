# -*- coding: utf-8 -*-
"""公式列保护(规格书 T09) + 修复后模板列位反向断言。

规格书铁律 1: 差异列 + 推广进店率/ROI 由**用户 Excel 公式**维护, 程序只读不写。
本测试两层验证(合成夹具, **不碰**用户真实的 17MB 模板):
  ① 三平台映射产物里**根本不含**任何禁写列(列位写错时会在这里炸);
  ② 就算有人硬把禁写列塞进 write_set, fill 引擎也必须**一格都不写**(公式仍在)。
反向断言: 修复后模板的核心写入列确实在写集里(列位没写反)。

夹具里的列头 = 「修复后模板」第 5 行快照(内联, 见 refs/模板列头全表.md); 禁写列的公式
= 模板第 6 行真实公式的等价形式(差异列 = 左 − 右, 推广率列 = IFERROR)。
"""
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))

import openpyxl                                                          # noqa: E402
from merchant_report import fill as F                                    # noqa: E402
from merchant_report.platforms import jd_mapping as jm                   # noqa: E402
from merchant_report.platforms import meituan_mapping as mm              # noqa: E402
from merchant_report.platforms import taobao_mapping as tm               # noqa: E402

DATE = "2026-09-19"

# ---------- 修复后模板列头快照(第 5 行; 只列本测试涉及的列) ----------
HEADERS = {
    # 美团主站流量 L~AR(本店/对比/差异)
    "L": "曝光人数", "M": "商圈TOP曝光人数", "N": "差异",
    "O": "进店人数", "P": "商圈TOP进店人数", "Q": "差异",
    "R": "进店转化率", "S": "商圈TOP进店转化率", "T": "差异",
    "U": "下单人数", "V": "商圈TOP下单人数", "W": "差异",
    "X": "下单转化率", "Y": "商圈TOP下单转化率", "Z": "差异",
    "AA": "曝光人数", "AB": "商圈TOP曝光人数", "AC": "差异",
    "AD": "进店人数", "AE": "商圈TOP进店人数", "AF": "差异",
    "AG": "下单转化率", "AH": "商圈TOP下单转化率", "AI": "差异",
    "AJ": "曝光人数", "AK": "商圈TOP曝光人数", "AL": "差异",
    "AM": "进店人数", "AN": "商圈TOP进店人数", "AO": "差异",
    "AP": "下单转化率", "AQ": "商圈TOP下单转化率", "AR": "差异",
    # 美团拼好饭流量 AX~CD
    "AX": "曝光人数", "AY": "商圈TOP曝光人数", "AZ": "差异",
    "BA": "进店人数", "BB": "商圈TOP进店人数", "BC": "差异",
    "BD": "进店转化率", "BE": "商圈TOP进店转化率", "BF": "差异",
    "BG": "下单人数", "BH": "商圈TOP下单人数", "BI": "差异",
    "BJ": "下单转化率", "BK": "商圈TOP下单转化率", "BL": "差异",
    "BM": "曝光人数", "BN": "商圈TOP曝光人数", "BO": "差异",
    "BP": "进店人数", "BQ": "商圈TOP进店人数", "BR": "差异",
    "BS": "下单转化率", "BT": "商圈TOP下单转化率", "BU": "差异",
    "BV": "曝光人数", "BW": "商圈TOP曝光人数", "BX": "差异",
    "BY": "进店人数", "BZ": "商圈TOP进店人数", "CA": "差异",
    "CB": "下单转化率", "CC": "商圈TOP下单转化率", "CD": "差异",
    # 美团推广 CE~CJ(CH/CI 公式列)
    "CE": "推广消耗金额", "CF": "总曝光量", "CG": "总进店量",
    "CH": "推广进店率", "CI": "推广ROI", "CJ": "推广/充值金额",
    # 淘宝主站流量 DM~ES
    "DM": "曝光人数", "DN": "商圈TOP曝光人数", "DO": "差异",
    "DP": "进店人数", "DQ": "商圈TOP进店人数", "DR": "差异",
    "DS": "进店转化率", "DT": "商圈TOP进店转化率", "DU": "差异",
    "DV": "下单人数", "DW": "商圈TOP下单人数", "DX": "差异",
    "DY": "下单转化率", "DZ": "商圈TOP下单转化率", "EA": "差异",
    "EB": "曝光人数", "EC": "商圈TOP曝光人数", "ED": "差异",
    "EE": "进店人数", "EF": "商圈TOP进店人数", "EG": "差异",
    "EH": "下单转化率", "EI": "商圈TOP下单转化率", "EJ": "差异",
    "EK": "曝光人数", "EL": "商圈TOP曝光人数", "EM": "差异",
    "EN": "进店人数", "EO": "商圈TOP进店人数", "EP": "差异",
    "EQ": "下单转化率", "ER": "商圈TOP下单转化率", "ES": "差异",
    # 淘宝爆品团流量 EY~GE
    "ET": "爆品团营业额", "EU": "爆品团营业收入", "EV": "爆品团订单量",
    "EW": "爆品团单均价", "EX": "无效订单",
    "EY": "曝光人数", "EZ": "商圈TOP曝光人数", "FA": "差异",
    "FB": "进店人数", "FC": "商圈TOP进店人数", "FD": "差异",
    "FE": "进店转化率", "FF": "商圈TOP进店转化率", "FG": "差异",
    "FH": "下单人数", "FI": "商圈TOP下单人数", "FJ": "差异",
    "FK": "下单转化率", "FL": "商圈TOP下单转化率", "FM": "差异",
    "FN": "曝光人数", "FO": "商圈TOP曝光人数", "FP": "差异",
    "FQ": "进店人数", "FR": "商圈TOP进店人数", "FS": "差异",
    "FT": "下单转化率", "FU": "商圈TOP下单转化率", "FV": "差异",
    "FW": "曝光人数", "FX": "商圈TOP曝光人数", "FY": "差异",
    "FZ": "进店人数", "GA": "商圈TOP进店人数", "GB": "差异",
    "GC": "下单转化率", "GD": "商圈TOP下单转化率", "GE": "差异",
    # 淘宝推广 GF~GK(GI/GJ 公式列)
    "GF": "推广消耗金额", "GG": "推广曝光量", "GH": "推广进店量",
    "GI": "推广进店率", "GJ": "推广ROI", "GK": "推广/充值金额",
    # 京东 HH~HJ(无公式列)
    "HH": "营业额", "HI": "营业收入", "HJ": "订单量",
}

# ---------- 规格书铁律 1 的禁写列(全部) ----------
FORBIDDEN_MEITUAN = ["N", "Q", "T", "W", "Z", "AC", "AF", "AI", "AL", "AO", "AR",
                     "AZ", "BC", "BF", "BI", "BL", "BO", "BR", "BU", "BX", "CA", "CD",
                     "CH", "CI"]
FORBIDDEN_TAOBAO = ["DO", "DR", "DU", "DX", "EA", "ED", "EG", "EJ", "EM", "EP", "ES",
                    "FA", "FD", "FG", "FJ", "FM", "FP", "FS", "FV", "FY", "GB", "GE",
                    "GI", "GJ"]
# 模板第 6 行真实公式的等价形式(差异列 = 左边本店列 − 右边对比列)
FORMULAS = {
    "CH": '=IFERROR(CG6/CF6,0)', "CI": '=IFERROR(I6/CE6,0)',
    "GI": '=IFERROR(GH6/GG6,0)', "GJ": '=IFERROR(#REF!/GF6,0)',
}
# 差异列: 公式 = 左邻(本店) − 右邻(对比); 由 HEADERS 相邻关系推导太绕 → 直接列出三列一组
for _base in ("L", "O", "R", "U", "X", "AA", "AD", "AG", "AJ", "AM", "AP"):
    _i = mm.col_index(_base)
    _a, _b, _c = F.GL(_i + 1), F.GL(_i + 2), F.GL(_i + 3)
    FORMULAS[_c] = f"={_a}6-{_b}6"
for _base in ("AX", "BA", "BD", "BG", "BJ", "BM", "BP", "BS", "BV", "BY", "CB"):
    _i = mm.col_index(_base)
    FORMULAS[F.GL(_i + 3)] = f"={F.GL(_i + 1)}6-{F.GL(_i + 2)}6"
for _base in ("DM", "DP", "DS", "DV", "DY", "EB", "EE", "EH", "EK", "EN", "EQ"):
    _i = mm.col_index(_base)
    FORMULAS[F.GL(_i + 3)] = f"={F.GL(_i + 1)}6-{F.GL(_i + 2)}6"
for _base in ("EY", "FB", "FE", "FH", "FK", "FN", "FQ", "FT", "FW", "FZ", "GC"):
    _i = mm.col_index(_base)
    FORMULAS[F.GL(_i + 3)] = f"={F.GL(_i + 1)}6-{F.GL(_i + 2)}6"

# 核心写入列(反向断言: 列位写反/写漏会在这里炸)
CORE_TAOBAO = ("DM", "DN", "EB", "EE", "EH", "ET", "EY", "GF", "GG", "GH", "GK")
CORE_MEITUAN = ("L", "M", "AG", "AP", "BS", "CB", "AH", "AQ", "BT", "CC", "CE", "CJ")
CORE_JD = ("HH", "HI", "HJ")


# 商圈采集(capture_circle 的形状): 整体/新客/老客 三组(新客/老客组没有"进店转化率"键,
# 其第 3 列在修复后模板里是「下单转化率」)。
CAPTURED = {
    "main": {"整体": {"曝光": 2341, "进店": 282, "进店转化率": 0.1205,
                    "下单": 114, "下单转化率": 0.4043},
             "新客": {"曝光": 1871, "进店": 181, "进店转化率": 0.0968,
                    "下单": 51, "下单转化率": 0.2818},
             "老客": {"曝光": 469, "进店": 100, "进店转化率": 0.2132,
                    "下单": 61, "下单转化率": 0.61}},
    "bao": {"整体": {"曝光": 898, "进店": 53, "进店转化率": 0.059,
                   "下单": 41, "下单转化率": 0.7736},
            "新客": {"曝光": 804, "进店": 33, "进店转化率": 0.041,
                   "下单": 12, "下单转化率": 0.3636},
            "老客": {"曝光": 93, "进店": 19, "进店转化率": 0.2043,
                   "下单": 14, "下单转化率": 0.7368}}}


def taobao_write_set():
    """合成: 全部报表 + 爆品团报表 + 逐店 capture(含推广) → 真实映射产物。"""
    all_row = {"营业额": 1000, "收入": 900, "有效订单": 50,
               "曝光人数": 5354, "进店人数": 301, "下单人数": 89,
               "新客曝光人数": 4571, "新客进店人数": 204, "新客下单人数": 53,
               "老客曝光人数": 783, "老客进店人数": 97, "老客下单人数": 36}
    bom_row = {"营业额": 200, "收入": 180, "有效订单": 10, "单均实付": 30, "无效订单": 1,
               "曝光人数": 2612, "进店人数": 39, "进店转化率": 0.015,
               "下单人数": 11, "下单转化率": 0.282,
               "新客曝光人数": 2398, "新客进店人数": 30, "新客下单转化率": 0.233,
               "老客曝光人数": 214, "老客进店人数": 9, "老客下单转化率": 0.444}
    cap = {"外卖_全部顾客": {"shop": {"exp": 5354, "ent": 301, "ord": 89,
                                    "ent_rate": 0.056, "ord_rate": 0.296},
                           "top10": {"exp": 6037, "ent": 630, "ord": 152,
                                     "ent_rate": 0.104, "ord_rate": 0.241}},
           "爆品团_全部顾客": {"shop": {}, "top10": {"exp": 946, "ent": 38, "ord": 10,
                                                   "ent_rate": 0.040, "ord_rate": 0.263}},
           "segments": {"外卖": {"新客": {"top10": {"exp": 4983, "ent": 432, "ord": 88,
                                                   "ent_rate": 0.087, "ord_rate": 0.204}},
                              "老客": {"top10": {"exp": 1054, "ent": 198, "ord": 64,
                                                 "ent_rate": 0.188, "ord_rate": 0.323}}},
                        "爆品团": {"新客": {"top10": {"exp": 841, "ent": 25, "ord": 4,
                                                      "ent_rate": 0.030, "ord_rate": 0.160}},
                                "老客": {"top10": {"exp": 105, "ent": 14, "ord": 6,
                                                   "ent_rate": 0.133, "ord_rate": 0.429}}}},
           "customer_dims_ok": True,
           "外卖_新客": {"shop": {"exp": 4571, "ent": 204, "ord": 53,
                                 "ent_rate": 0.045, "ord_rate": 0.260}},
           "外卖_老客": {"shop": {"exp": 783, "ent": 97, "ord": 36,
                                 "ent_rate": 0.124, "ord_rate": 0.371}},
           "promo": {"GF": 151.58, "GG": 4843, "GH": 133, "GK": 200.0}}
    return tm.build_write_set(all_row, bom_row, cap)


def meituan_write_set():
    """合成: 全部业务 / 拼好饭 报表行 + 商圈采集 + 推广流水 → 真实映射产物。

    与 orchestrator 的真实组合一致: build_write_set(报表) + circle_write_set_all(商圈) + 推广列。
    """
    common = {"曝光人数": 2589, "入店人数": 150, "入店转化率": 0.0579,
              "下单转化率": 0.42, "下单人数": 63,
              "曝光新客": 2149, "入店新客": 104, "新客下单转化率": 0.2115, "下单新客": 22,
              "曝光老客": 440, "入店老客": 46, "老客下单转化率": 0.8913, "下单老客": 41,
              "营业收入": 845.84, "有效订单": 75, "优惠前总额": 900, "平台活动补贴": 55,
              "取消订单": 1, "商责取消订单": 0, "店铺分": 4.8}
    bao = {"曝光人数": 1275, "入店人数": 80, "入店转化率": 0.0627, "下单转化率": 0.5,
           "下单人数": 40, "曝光新客": 1077, "入店新客": 46, "新客下单转化率": 0.3043,
           "下单新客": 14, "曝光老客": 198, "入店老客": 34, "老客下单转化率": 0.7647,
           "下单老客": 26, "营业收入": 500, "有效订单": 40, "优惠前总额": 520,
           "实付单均价": 12, "活动补贴": 20}
    ws = mm.build_write_set(common, bao)
    cw = mm.circle_write_set_all(CAPTURED)            # 商圈 TOP(仅单店模式)
    ws.update(cw["main"])
    ws.update(cw["bao"])
    ws.update({"CE": 151.58, "CF": 4843, "CG": 133, "CJ": 200.0})   # 推广流水(账号/单店)
    return ws


def build_sheet():
    """合成模板(表头第 5 行 + 禁写列第 6 行公式 + A6 日期) → (wb, ws)。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "测试店"
    for col, name in HEADERS.items():
        ws[f"{col}5"] = name
    for col, f in FORMULAS.items():
        ws[f"{col}6"] = f
    ws["A6"] = datetime.datetime.strptime(DATE, "%Y-%m-%d")
    return wb, ws


def test_mappings_never_emit_forbidden_columns():
    """① 三平台映射产物里不得出现任何禁写列(列位写错会在这里炸)。"""
    cases = [("淘宝", taobao_write_set(), FORBIDDEN_TAOBAO),
             ("美团", meituan_write_set(), FORBIDDEN_MEITUAN)]
    for tag, ws_, forbidden in cases:
        bad = sorted(c for c in forbidden if ws_.get(c) is not None)
        assert not bad, f"{tag}: 映射把值写进了模板公式列 {bad}"
        print(f"✅ {tag}: 映射产物不含禁写列({len(forbidden)} 列全部干净)")
    # 京东不涉及公式列(3 列都是静态值)
    jd = jm.build_write_set({"营业额": 258, "收入": 97.51, "有效订单": 10})
    assert all(jd.get(c) is not None for c in CORE_JD), jd
    print("✅ 京东: HH~HJ 3 列无公式列(全为静态值)")


def test_circle_write_set_never_emits_forbidden():
    """商圈(对比列)产物也不得落到禁写列上。"""
    captured = CAPTURED
    per = mm.circle_write_set_all(captured)
    for biz, vals in per.items():
        bad = sorted(c for c in FORBIDDEN_MEITUAN if vals.get(c) is not None)
        assert not bad, f"{biz}: 商圈值落进公式列 {bad}"
    # 新客/老客第 3 列 = 下单转化率(不是进店转化率)
    assert per["main"]["AH"] == 0.2818 and per["main"]["AQ"] == 0.61, per["main"]
    assert per["bao"]["BT"] == 0.3636 and per["bao"]["CC"] == 0.7368, per["bao"]
    # 整体组本来就有「下单转化率」直列, 不受影响
    assert per["main"]["Y"] == 0.4043 and per["bao"]["BK"] == 0.7736, per
    print("✅ 商圈映射: 不含禁写列; AH/AQ/BT/CC = 下单转化率(旧实现填的是进店转化率)")


def test_forbidden_columns_untouched_even_if_in_write_set():
    """② 就算把禁写列塞进 write_set, fill 引擎也必须一格都不写(公式保持原样)。"""
    wb, ws = build_sheet()
    poison = {c: 99999 for c in FORBIDDEN_MEITUAN + FORBIDDEN_TAOBAO}
    before = {c: ws[f"{c}6"].value for c in FORMULAS}
    F._write_into(ws, DATE, poison)
    leaked = [c for c in FORMULAS if ws[f"{c}6"].value != before[c]]
    assert not leaked, f"公式列被覆盖: {[(c, ws[f'{c}6'].value) for c in leaked]}"
    assert all(isinstance(ws[f"{c}6"].value, str) and str(ws[f"{c}6"].value).startswith("=")
               for c in FORMULAS), "禁写列必须仍是公式"
    print(f"✅ 硬塞禁写列: {len(FORMULAS)} 个公式格一格未动(含 CH/CI/GI/GJ 与全部差异列)")
    wb.close()


def test_meituan_rate_columns_and_fallback():
    """任务 2 的 4 个率值列(AG/AP/BS/CB): 有直列取直列; 缺直列自算; 都没有则留空 + 告警。"""
    ws = meituan_write_set()
    assert ws["AG"] == 0.2115 and ws["AP"] == 0.8913, (ws["AG"], ws["AP"])
    assert ws["BS"] == 0.3043 and ws["CB"] == 0.7647, (ws["BS"], ws["CB"])
    # 与「进店转化率」不同 → 证明取的不是同漏斗上一级指标
    assert ws["AG"] != 0.0437 and ws["AP"] != 0.1285

    # ② 报表缺「新客下单转化率」直列 → 用 下单新客/入店新客 自算(=22/104)
    all2 = {"入店新客": 104, "下单新客": 22, "入店老客": 46, "下单老客": 41}
    mm.NAME_FALLBACKS.clear()
    ws2 = mm.build_write_set(all2, None)
    assert ws2["AG"] == round(22 / 104, 6) and ws2["AP"] == round(41 / 46, 6), (ws2["AG"], ws2["AP"])
    # ③ 连人数都没有 → 留空 + 记一笔待人工核对(宁缺勿错, 绝不退回「入店转化率」)
    mm.NAME_FALLBACKS.clear()
    ws3 = mm.build_write_set({}, None)
    assert ws3["AG"] is None and ws3["AP"] is None, (ws3["AG"], ws3["AP"])
    assert any("AG=" in x for x in mm.NAME_FALLBACKS), mm.NAME_FALLBACKS[:8]
    print("✅ 美团率值列: AG=21.15%/AP=89.13%/BS=30.43%/CB=76.47%; 缺直列自算, 全缺留空+告警")


def test_core_columns_actually_written():
    """反向断言: 修复后模板的核心列确实被写进去了(列位写反/写漏会在这里炸)。"""
    wb, ws = build_sheet()
    # 京东列在夹具里也建上
    ws["HH5"], ws["HI5"], ws["HJ5"] = "营业额", "营业收入", "订单量"
    sets = {"淘宝": taobao_write_set(), "美团": meituan_write_set(),
            "京东": jm.build_write_set({"营业额": 258, "收入": 97.51, "有效订单": 10})}
    cores = {"淘宝": CORE_TAOBAO, "美团": CORE_MEITUAN, "京东": CORE_JD}
    for tag, ws_ in sets.items():
        missing = [c for c in cores[tag] if ws_.get(c) is None]
        assert not missing, f"{tag}: 核心列没写出来 {missing}"
    # 真写进模板 → 用 fill 引擎落盘一次, 断言核心格有值
    merged = {}
    for ws_ in sets.values():
        merged.update({k: v for k, v in ws_.items() if v is not None})
    F._write_into(ws, DATE, merged)
    empty = [c for c in (CORE_TAOBAO + CORE_MEITUAN + CORE_JD) if ws[f"{c}6"].value in (None, "")]
    assert not empty, f"核心列写进去却是空的: {empty}"
    # 抽 3 个关键值核对(与规格书 §6 验收口径一致)
    assert ws["DM6"].value == 5354 and ws["ET6"].value == 200 and ws["GF6"].value == 151.58, \
        (ws["DM6"].value, ws["ET6"].value, ws["GF6"].value)
    assert ws["AG6"].value == 0.2115 and ws["AP6"].value == 0.8913, (ws["AG6"].value, ws["AP6"].value)
    assert ws["HH6"].value == 258 and ws["HI6"].value == 97.51 and ws["HJ6"].value == 10, \
        (ws["HH6"].value, ws["HI6"].value, ws["HJ6"].value)
    print("✅ 核心列确实写入: 淘宝 DM=5354/ET=200/GF=151.58 · 美团 AG=21.15%/AP=89.13% · 京东 HH/HI/HJ=258/97.51/10")
    wb.close()


def test_header_snapshot_matches_ref_table():
    """夹具的列头快照必须与 refs/模板列头全表.md 的「修复后模板」一致(防止夹具漂移)。

    只读参考文件; 不在(公开仓/CI 无 refs)则优雅跳过。
    """
    ref = pathlib.Path("/Users/demo/.hermes/cache/scratch/mrc-fix-1013/refs/模板列头全表.md")
    if not ref.exists():
        print("… refs 列头全表不在, 跳过对照")
        return
    want = {}
    for line in ref.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) == 6 and parts[0] and parts[0] != "列":
            want[parts[0]] = parts[5]
    bad = {c: (h, want.get(c)) for c, h in HEADERS.items() if c in want and want[c] and want[c] != h}
    assert not bad, f"夹具列头与权威表不符: {bad}"
    print(f"✅ 夹具列头与 refs/模板列头全表.md 逐列一致(核对 {len(HEADERS)} 列)")


def main():
    test_header_snapshot_matches_ref_table()
    test_mappings_never_emit_forbidden_columns()
    test_circle_write_set_never_emits_forbidden()
    test_forbidden_columns_untouched_even_if_in_write_set()
    test_meituan_rate_columns_and_fallback()
    test_core_columns_actually_written()
    print("\n✅ 公式列保护(差异列 + CH/CI + GI/GJ) 全绿")


if __name__ == "__main__":
    main()
