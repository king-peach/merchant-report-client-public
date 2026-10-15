# -*- coding: utf-8 -*-
"""模板结构自适应(任务 I): 「10.13 新版 / 旧版」双结构识别 + 列位平移 + 未知结构拒写。

背景(现场复现, 见 refs/repro_template_shift_result.txt): 旧版模板比 10.13 版**多一列
「平台活动补贴」**(旧版 DJ), 于是**从主站区起**旧版 = 新版列位 +1。内核 mapping 按新版
写死, 拿旧版模板跑会整体错 1 列(7 个值错 5 个, 另 2 个被旧版公式列吞掉)。

本测试全用**合成件**(不碰真实模板):
  ① detect_layout: 两版各判对; 锚点挪位/自相矛盾/证据不足 → unknown;
  ② legacy 写入: ≥DJ 的列整体 +1, 且旧版公式列(差异列/主站订单量)一格未被覆盖;
  ③ new 写入: 落点与现状**逐列一致**(平移不生效);
  ④ unknown: 拒写(所有目标格保持原值) + warn 日志 + `skipped_unknown_layout=True`;
  ⑤ fill_many / fill_template 两条路径都覆盖, 含回读校验(拿平移后的 write_set 核对);
  ⑥ translate_col 边界: DJ→DK、DI→DI(不动)、A→A、IF→IG。
"""
import datetime
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
import openpyxl                                                              # noqa: E402
from merchant_report import fill as F                                        # noqa: E402

DATE = "2026-09-19"
SN = "测试店"
FAILS = []

# 探针字典(任务书 §四 4.1.2 原文给的 7 列)
WS = {"DM": 1111, "DN": 2222, "DP": 3333, "DQ": 4444, "HH": 5555, "HI": 6666, "HJ": 7777}
# 平移到旧版后应当落在哪(每列 +1)
LEGACY_LANDING = {"DN": 1111, "DO": 2222, "DQ": 3333, "DR": 4444,
                  "HI": 5555, "HJ": 6666, "HK": 7777}
NEW_LANDING = {"DM": 1111, "DN": 2222, "DP": 3333, "DQ": 4444,
               "HH": 5555, "HI": 6666, "HJ": 7777}


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


class FakeSink:
    """替身事件出口: 收集 (level, msg) 供断言(fill.set_log_sink 登记)。"""

    def __init__(self):
        self.msgs = []

    def log(self, msg, level="info"):
        self.msgs.append((level, msg))


# ---------------- 合成夹具 ----------------
# 列位严格照真模板「铁道」第 5 行 / 第 1 行(分区标题)快照抄, 保证夹具与真机同构。

NEW_HDR5 = {"DG": "总营业额", "DH": "总营业收入", "DI": "总订单量",
            "DJ": "主站营业额", "DK": "主站营业收入", "DL": "主站订单量",
            "DM": "曝光人数", "DN": "商圈TOP曝光人数", "DO": "差异",
            "DP": "进店人数", "DQ": "商圈TOP进店人数", "DR": "差异",
            "HH": "营业额", "HI": "营业收入", "HJ": "订单量"}
NEW_FORMULAS = {"DJ": "=DG6-ET6", "DK": "=DH6-EU6", "DL": "=DI6-EV6",
                "DO": "=DM6-DN6", "DR": "=DP6-DQ6"}

LEGACY_HDR5 = {"DG": "总营业额", "DH": "总营业收入", "DI": "总订单量",
               "DJ": "平台活动补贴", "DK": "主站营业额", "DL": "主站营业收入",
               "DM": "主站订单量", "DN": "曝光人数", "DO": "商圈TOP曝光人数",
               "DP": "差异", "DQ": "进店人数", "DR": "商圈TOP进店人数",
               "HI": "营业额", "HJ": "营业收入", "HK": "订单量"}
# 旧版公式列 = 新版公式列右移 1(主站订单量/两个差异列); 新版的 DJ(主站营业额) 在旧版是
# **静态**列(平台活动补贴), 新版 DM/DP 在旧版是要保护的公式列 —— 正是"错列写入"的现场。
LEGACY_FORMULAS = {"DK": "=DG6-EU6", "DL": "=DH6-EV6", "DM": "=DI6-EW6", "DP": "=DN6-DO6"}


def mk(path, layout="new", hdr5=None, formulas=None, anchors=None, a6=DATE):
    """造一份迷你模板: Sheet1/Sheet2(主表) + 一张门店表(表头第 5 行/数据第 6 行起)。

    anchors = {列: 锚点文本} 覆盖默认锚点(用于构造 unknown 件)。
    """
    wb = openpyxl.Workbook()
    wb.active.title = "Sheet1"
    wb.create_sheet("Sheet2")
    ws = wb.create_sheet(SN)
    for col, name in (hdr5 if hdr5 is not None else
                      (NEW_HDR5 if layout == "new" else LEGACY_HDR5)).items():
        ws[f"{col}5"] = name
    if anchors is None:
        # 真模板的锚点分布: 两个分区标题在第 1 行, 其余在列表头行(第 5 行)
        anchors = ({"DJ5": "主站营业额", "HH1": "京东外卖数据", "HN1": "美团管家",
                    "DH5": "总营业收入", "DI5": "总订单量"} if layout == "new" else
                   {"DK5": "主站营业额", "HI1": "京东外卖数据", "HO1": "美团管家",
                    "DJ5": "平台活动补贴", "DH5": "总营业收入", "DI5": "总订单量"})
    for ref, txt in anchors.items():
        ws[ref] = txt
    for col, f in (formulas if formulas is not None else
                   (NEW_FORMULAS if layout == "new" else LEGACY_FORMULAS)).items():
        ws[f"{col}6"] = f
    ws["A6"] = datetime.datetime.strptime(a6, "%Y-%m-%d")
    wb.save(path)
    return path


def sheet(path, sn=SN):
    return openpyxl.load_workbook(path)[sn]


# ---------------- ① 结构识别 ----------------

def test_detect_layout_both_versions():
    print("【1】detect_layout: 两版分别判 new / legacy")
    d = tempfile.mkdtemp()
    check("新版件 → new", F.detect_layout(sheet(mk(f"{d}/new.xlsx", "new"))) == "new")
    check("旧版件 → legacy", F.detect_layout(sheet(mk(f"{d}/old.xlsx", "legacy"))) == "legacy")
    # 旧版件即使「平台活动补贴」被删掉(查不到 = 不投票), 也不影响判定
    old2 = dict(LEGACY_HDR5)
    old2.pop("DJ")
    a2 = {"DK5": "主站营业额", "HI1": "京东外卖数据", "HO1": "美团管家",
          "DH5": "总营业收入", "DI5": "总订单量"}
    check("旧版件缺「平台活动补贴」→ 仍判 legacy(查不到不投票)",
          F.detect_layout(sheet(mk(f"{d}/old2.xlsx", "legacy", hdr5=old2, anchors=a2,
                                   formulas={"DK": "=DG6-EU6", "DM": "=DI6-EW6"}))) == "legacy")
    # 边界锚缺失(不否决, 只记一笔)
    a3 = {"DJ5": "主站营业额", "HH1": "京东外卖数据", "HN1": "美团管家"}
    check("新版件缺边界锚(总订单量/总营业收入)→ 仍判 new(缺失不否决)",
          F.detect_layout(sheet(mk(f"{d}/new2.xlsx", "new", anchors=a3))) == "new")


def test_detect_layout_unknown_cases():
    print("【2】detect_layout: 判不出来时必须 unknown(宁可拒写也不猜)")
    d = tempfile.mkdtemp()
    # ① 探针锚点被挪到别的列 → 与任何版本都对不上
    a = {"DJ5": "主站营业额", "HH1": "京东外卖数据", "HN1": "美团管家"}
    a_bad = dict(a)
    a_bad.pop("DJ5")
    a_bad["DZ5"] = "主站营业额"                      # 挪到 DZ(不是 DJ 也不是 DK)
    check("主站营业额锚点挪到别处 → unknown",
          F.detect_layout(sheet(mk(f"{d}/u1.xlsx", "new", anchors=a_bad))) == "unknown")
    # ② 「平台活动补贴」在, 但探针一致指 new → 自相矛盾
    a2 = dict(a)
    a2["EA5"] = "平台活动补贴"
    check("「平台活动补贴」存在但探针指 new → unknown",
          F.detect_layout(sheet(mk(f"{d}/u2.xlsx", "new", anchors=a2))) == "unknown")
    # ③ 只剩 1 个探针可查 → 证据不足
    check("只剩 1 个探针 → unknown",
          F.detect_layout(sheet(mk(f"{d}/u3.xlsx", "new", anchors={"DJ5": "主站营业额"}))) == "unknown")
    # ④ 边界锚位错(说明平移起点不是 DJ) → unknown
    a4 = {"DJ5": "主站营业额", "HH1": "京东外卖数据", "HN1": "美团管家",
          "DH5": "总营业收入", "DZ5": "总订单量"}     # 总订单量不在 DI
    check("边界锚「总订单量」不在 DI(平移起点可疑) → unknown",
          F.detect_layout(sheet(mk(f"{d}/u4.xlsx", "new", anchors=a4))) == "unknown")
    # ⑤ 一个探针都没有(如汇总/看板表) → unknown
    check("无任何探针的表(汇总表) → unknown",
          F.detect_layout(sheet(mk(f"{d}/u5.xlsx", "new",
                                   anchors={"AA5": "某个无关标题"}))) == "unknown")


# ---------------- ② / ③ 写入落点 ----------------

def _write(path, mode="top", fn="fill_many"):
    if fn == "fill_many":
        return F.fill_many(path, [(SN, DATE, dict(WS))], mode=mode)[0][2]
    return F.fill_template(path, DATE, dict(WS), mode=mode, sheet_name=SN)


def test_legacy_write_shifts_columns():
    print("【3】legacy 写入: ≥DJ 整体 +1, 旧版公式列一格未动")
    for fn in ("fill_many", "fill_template"):
        p = mk(f"{tempfile.mkdtemp()}/old_{fn}.xlsx", "legacy")
        r = _write(p, fn=fn)
        check(f"[{fn}] 判定 legacy", r["layout"] == "legacy", r["layout"])
        check(f"[{fn}] filled=7 skipped_formula=0",
              (r["filled"], r["skipped_formula"]) == (7, 0), (r["filled"], r["skipped_formula"]))
        check(f"[{fn}] 回读校验干净(拿平移后的 write_set 核对)", r["reread_bad"] == [], r["reread_bad"])
        got = {}
        for col in ("DM", "DN", "DO", "DP", "DQ", "DR", "HH", "HI", "HJ", "HK"):
            got[col] = sheet(p)[f"{col}6"].value
        check(f"[{fn}] 落点 = 旧版列位 {LEGACY_LANDING}",
              all(got[c] == v for c, v in LEGACY_LANDING.items()), got)
        check(f"[{fn}] 旧版公式列仍是公式(差异列/主站订单量没被覆盖)",
              got["DP"] == "=DN6-DO6" and got["DM"] == "=DI6-EW6", (got["DM"], got["DP"]))
        check(f"[{fn}] 新版列位未被写入(平移生效, 没有双写)",
              got["HH"] is None, got["HH"])
    # legacy 且真有平移 → 打一行 info(便于用户在日志里确认走的是旧版分支)
    sk = FakeSink()
    F.set_log_sink(sk)
    try:
        _write(mk(f"{tempfile.mkdtemp()}/old_log.xlsx", "legacy"))
    finally:
        F.set_log_sink(None)
    check("legacy 平移 → info 日志含「旧版」",
          any(l == "info" and "旧版" in m for l, m in sk.msgs), sk.msgs)


def test_new_write_unchanged():
    print("【4】new 写入: 落点与现状逐列一致(平移不生效)")
    for fn in ("fill_many", "fill_template"):
        p = mk(f"{tempfile.mkdtemp()}/new_{fn}.xlsx", "new")
        r = _write(p, fn=fn)
        check(f"[{fn}] 判定 new", r["layout"] == "new", r["layout"])
        check(f"[{fn}] filled=7 skipped_formula=0",
              (r["filled"], r["skipped_formula"]) == (7, 0), (r["filled"], r["skipped_formula"]))
        check(f"[{fn}] 回读校验干净", r["reread_bad"] == [], r["reread_bad"])
        got = {c: sheet(p)[f"{c}6"].value for c in NEW_LANDING}
        check(f"[{fn}] 落点 = 新版列位 {NEW_LANDING}",
              all(got[c] == v for c, v in NEW_LANDING.items()), got)
        check(f"[{fn}] 新版公式列(DJ 主站营业额)未被覆盖",
              sheet(p)["DJ6"].value == "=DG6-ET6", sheet(p)["DJ6"].value)
    # new 版不打日志(避免现有日志断言变动)
    sk = FakeSink()
    F.set_log_sink(sk)
    try:
        _write(mk(f"{tempfile.mkdtemp()}/new_log.xlsx", "new"))
    finally:
        F.set_log_sink(None)
    check("new 版不产生结构日志", sk.msgs == [], sk.msgs)


def test_unknown_refuses_write():
    print("【5】unknown 拒写: 一格不写 + warn 日志 + 显式标志")
    a = {"DJ5": "主站营业额", "HH1": "京东外卖数据", "HN1": "美团管家"}
    a.pop("DJ5")
    a["DZ5"] = "主站营业额"                          # 探针挪位 → unknown
    d = tempfile.mkdtemp()
    for fn in ("fill_many", "fill_template"):
        p = mk(f"{d}/u_{fn}.xlsx", "new", anchors=a)
        before = {c: sheet(p)[f"{c}6"].value for c in
                  ("DM", "DN", "DP", "DQ", "HH", "HI", "HJ", "DJ", "DO", "DR")}
        sk = FakeSink()
        F.set_log_sink(sk)
        try:
            r = _write(p, fn=fn)
        finally:
            F.set_log_sink(None)
        check(f"[{fn}] skipped_unknown_layout=True + layout=unknown + filled=0",
              r["skipped_unknown_layout"] is True and r["layout"] == "unknown" and
              r["filled"] == 0 and r["row"] is None, r)
        check(f"[{fn}] 回读校验跳过(没写就不校验)", r["reread_bad"] == [], r["reread_bad"])
        after = {c: sheet(p)[f"{c}6"].value for c in before}
        check(f"[{fn}] 所有目标格保持原值(一格未写)", after == before, after)
        check(f"[{fn}] 公式列仍是公式",
              after["DJ"] == "=DG6-ET6" and after["DO"] == "=DM6-DN6", after)
        check(f"[{fn}] warn 日志出现(写明不会猜列写错)",
              any(l == "warn" and "模板结构未识别" in m and SN in m for l, m in sk.msgs), sk.msgs)


# ---------------- ⑥ translate_col 边界 ----------------

def test_translate_col():
    print("【6】translate_col 边界")
    cases = [("DJ", "DK"), ("DI", "DI"), ("DH", "DH"), ("A", "A"), ("C", "C"),
             ("IF", "IG"), ("IA", "IB"), ("DK", "DL")]
    for col, want in cases:
        got = F.translate_col(col, "legacy")
        check(f"legacy: {col} → {want}", got == want, got)
    check("new 版一律原样(含 DJ/HH/IF)",
          all(F.translate_col(c, "new") == c for c, _ in cases))
    check("unknown 版一律原样", F.translate_col("DJ", "unknown") == "DJ")
    check("只对 ≥DJ 平移: DI(113) 不动 / DJ(114) 动",
          F.translate_col("DI", "legacy") == "DI" and F.translate_col("DJ", "legacy") == "DK")


def main():
    test_detect_layout_both_versions()
    test_detect_layout_unknown_cases()
    test_legacy_write_shifts_columns()
    test_new_write_unchanged()
    test_unknown_refuses_write()
    test_translate_col()
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败: {FAILS}")
        return 1
    print("✅ 模板结构自适应(双结构识别 / 列位平移 / 未知拒写) 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
