# -*- coding: utf-8 -*-
"""天气/温度**逐店化**(修复规格书 T01 / 铁律 9)单测 —— 全部合成夹具, 不联网。

覆盖:
  ① 门店明细解析「门店地址」列 → addr_by_name / addr_by_sheet; 老表(没这列) → 空 + 不崩;
  ② 地址 → 城市判定三分支(湘潭/长沙/未知) + 坐标;
  ③ 逐店取数 + 逐店写入: 一湘潭一长沙两家店 → 各自 Sheet 拿到各自的城市/天气;
     地址缺失的店 → 回退默认长沙 + warn(写明店名);
  ④ 天麓结构差异: 表头行少一行、数据从第 5 行开始 → 写入/回读不越界、不串行、不重复日期。
"""
import os
import pathlib
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
import openpyxl                                                              # noqa: E402
from merchant_report import fill as F                                        # noqa: E402
from merchant_report import weather as W                                     # noqa: E402
from merchant_report.platforms import meituan_mapping as mm                  # noqa: E402
from merchant_report.store_details import load_store_details                 # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------- ① 门店明细「门店地址」----------------

def mk_details(path, with_addr=True):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "data"
    if with_addr:
        ws.append(["淘宝闪购", "", "美团", "", "运营报表模板Sheet名", "门店地址"])
        ws.append(["门店名称", "门店唯一编号", "门店名称", "门店唯一编号", "Sheet名称", ""])
        ws.append(["示例柠檬茶(湘潭岚霞路店)", "4444444444", "示例（湘潭岚霞路店）", "33333333",
                   "岚霞路", "湘潭市岳塘区岚霞路279号"])
        ws.append(["示例柠檬茶(长沙高桥店)", "529930670", "示例（长沙高桥店）", "55555555",
                   "高桥", "长沙市雨花区民主路高桥现代商贸城"])
        ws.append(["示例柠檬茶(无地址店)", "999", "示例（无地址店）", "998", "无地址", ""])
    else:                                   # 老表: 没有「门店地址」列
        ws.append(["淘宝闪购", "", "美团", "", "运营报表模板Sheet名"])
        ws.append(["门店名称", "门店唯一编号", "门店名称", "门店唯一编号", "Sheet名称"])
        ws.append(["示例柠檬茶(湘潭岚霞路店)", "4444444444", "示例（湘潭岚霞路店）", "33333333", "岚霞路"])
    wb.save(path)
    wb.close()
    return path


def test_details_addr():
    print("【1】《门店明细》解析「门店地址」列")
    d = mk_details(os.path.join(tempfile.mkdtemp(), "明细.xlsx"), with_addr=True)
    got = load_store_details(d)
    mt = got["meituan"]
    check("美团 addr_by_name 湘潭岚霞路店", mt["addr_by_name"].get("示例湘潭岚霞路店") ==
          "湘潭市岳塘区岚霞路279号", mt["addr_by_name"])
    check("美团 addr_by_sheet[岚霞路] = 湘潭地址",
          mt["addr_by_sheet"].get("岚霞路") == "湘潭市岳塘区岚霞路279号", mt["addr_by_sheet"])
    check("顶层 addr_by_sheet 汇总(跨平台, 天气取数入口)",
          got["addr_by_sheet"].get("高桥", "").startswith("长沙市雨花区"), got.get("addr_by_sheet"))
    check("读不到地址的行 → 跳过该行地址(不猜)",
          "无地址" not in mt["addr_by_sheet"], mt["addr_by_sheet"])
    check("by_name/by_id 不受影响(向后兼容)",
          mt["by_name"].get("示例长沙高桥店") == "高桥" and mt["by_id"].get("55555555") == "高桥", mt)

    old = mk_details(os.path.join(tempfile.mkdtemp(), "老表.xlsx"), with_addr=False)
    got2 = load_store_details(old)
    check("老表(无地址列) → addr_by_name/addr_by_sheet 为空且不崩",
          not (got2["meituan"].get("addr_by_name") or {}) and
          not (got2["meituan"].get("addr_by_sheet") or {}) and
          got2["meituan"]["by_name"].get("示例湘潭岚霞路店") == "岚霞路", got2["meituan"])


# ---------------- ② 城市判定 ----------------

def test_city_of_address():
    print("【2】地址 → 城市判定(湘潭/长沙/未知)")
    check("湘潭", W.city_of_address("湘潭市岳塘区岚霞路279号") == "湘潭")
    check("长沙", W.city_of_address("长沙市雨花区民主路高桥现代商贸城") == "长沙")
    check("未知 → None(不猜)", W.city_of_address("株洲市天元区某路") is None and
          W.city_of_address("") is None and W.city_of_address(None) is None)
    check("AREA_ID 有湘潭 57773", W.AREA_ID.get("湘潭") == "57773", W.AREA_ID)
    check("coords_for: 湘潭用市级近似", W.coords_for("湘潭市…") == ("湘潭", 27.83, 112.94),
          W.coords_for("湘潭市…"))
    check("coords_for: 长沙沿用默认", W.coords_for("长沙市…") == ("长沙", 28.187, 112.921),
          W.coords_for("长沙市…"))
    check("coords_for: 认不出 → 默认长沙(调用方另记 warn)",
          W.coords_for("株洲市…")[0] == "长沙" and W.coords_for(None)[0] == "长沙",
          W.coords_for("株洲市…"))


# ---------------- ③ 逐店取数 + 逐店写入 ----------------

def _fake_fetcher(calls):
    """替身: 不打网络 —— 按 city/坐标造假值, 并记录 (date, city) 调用。"""
    def fake(date, lat, lon, biz_hours=None, city="长沙"):
        calls.append((date, city))
        return (f"{city}晴{date[8:]}", f"{city}20~25℃")
    return fake


def test_per_store_weather_and_write():
    print("【3】逐店取数 + 逐店写入(一湘潭一长沙)")
    calls = []
    orig = W.fetch_cached
    W.fetch_cached = _fake_fetcher(calls)      # 替身: 全程不联网
    try:
        W.clear_cache()
        targets = {"岚霞路": "湘潭市岳塘区岚霞路279号",
                   "高桥": "长沙市雨花区民主路高桥现代商贸城",
                   "无地址店": ""}
        by_sheet, warns = W.fetch_map(targets, ["2026-10-13"], label="美团门店")
        check("三家店各有一天的天气", set(by_sheet) == set(targets), by_sheet)
        check("岚霞路 = 湘潭值", by_sheet["岚霞路"]["2026-10-13"][0] == "湘潭晴13", by_sheet["岚霞路"])
        check("高桥 = 长沙值", by_sheet["高桥"]["2026-10-13"][0] == "长沙晴13", by_sheet["高桥"])
        check("缺地址 → 回退长沙 + warn 写明店名",
              by_sheet["无地址店"]["2026-10-13"][0] == "长沙晴13" and
              any("无地址店" in w for w in warns), warns)
        check("同城同日只打一次接口(长沙两家共享)",
              len([c for c in calls if c[1] == "长沙"]) == 2 and
              len({c for c in calls}) == 2, calls)

        # 写入: 合成模板(Sheet1/Sheet2 主表 + 两门店表), 用美团映射的 C/D 列
        d = tempfile.mkdtemp()
        tpl = os.path.join(d, "模板.xlsx")
        wb = openpyxl.Workbook()
        wb.active.title = "Sheet1"
        wb.create_sheet("Sheet2")
        for sn in ("岚霞路", "高桥"):
            ws = wb.create_sheet(sn)
            ws["A5"], ws["C5"], ws["D5"] = "日期", "天气", "温度"
            ws["A6"] = datetime.strptime("2026-10-12", "%Y-%m-%d")   # 已有前一天 → 追加到第 7 行
        wb.save(tpl)

        jobs = []
        for sn in ("岚霞路", "高桥"):
            wx = by_sheet[sn]["2026-10-13"]          # 复用上一步的替身结果(不再打接口)
            jobs.append((sn, "2026-10-13", mm.build_write_set({}, None, weather=wx)))
        F.fill_many(tpl, jobs, mode="auto")
        wb2 = openpyxl.load_workbook(tpl)
        check("岚霞路 C 列 = 湘潭天气", wb2["岚霞路"]["C7"].value == "湘潭晴13",
              wb2["岚霞路"]["C7"].value)
        check("高桥 C 列 = 长沙天气", wb2["高桥"]["C7"].value == "长沙晴13",
              wb2["高桥"]["C7"].value)
        check("两店温度也不同(不再是全表共用一组)",
              wb2["岚霞路"]["D7"].value == "湘潭20~25℃" and wb2["高桥"]["D7"].value == "长沙20~25℃",
              (wb2["岚霞路"]["D7"].value, wb2["高桥"]["D7"].value))
        wb2.close()
    finally:
        W.fetch_cached = orig
        W.clear_cache()


# ---------------- ④ 天麓结构差异 ----------------

def mk_tianlu_template(path):
    """合成夹具: 常规门店表(表头第 5 行/数据第 6 行) + 「天麓」式旧结构(表头第 4 行/数据第 5 行)。"""
    wb = openpyxl.Workbook()
    wb.active.title = "Sheet1"
    wb.create_sheet("Sheet2")
    ws = wb.create_sheet("岚霞路")
    ws["A5"], ws["B5"], ws["C5"] = "日期", "星期", "天气"
    ws["B6"] = '=TEXT(A6,"AAAA")'                       # 公式列(第 6 行起)
    ws["E6"] = "=C6*2"
    ws["A6"] = datetime.strptime("2026-10-12", "%Y-%m-%d")
    tl = wb.create_sheet("天麓")
    tl["A1"] = "3月示例外卖平台数据"
    tl["A4"], tl["B4"], tl["C4"] = "日期", "星期", "天气"    # 表头比常规表**少一行**
    tl["B5"] = '=TEXT(A5,"AAAA")'
    tl["E5"] = "=C5*2"
    tl["A5"] = datetime.strptime("2026-10-12", "%Y-%m-%d")  # 数据从第 5 行开始
    tl["C5"] = "旧值"
    wb.save(path)
    return path


def test_tianlu_structure():
    print("【4】天麓式结构(表头少一行 / 数据从第 5 行起)")
    tpl = mk_tianlu_template(os.path.join(tempfile.mkdtemp(), "模板.xlsx"))
    wb = openpyxl.load_workbook(tpl)
    check("data_start_row: 常规表 = 6", F.data_start_row(wb["岚霞路"]) == 6,
          F.data_start_row(wb["岚霞路"]))
    check("data_start_row: 天麓式旧结构 = 5", F.data_start_row(wb["天麓"]) == 5,
          F.data_start_row(wb["天麓"]))
    # ① auto: 已有该日期 → 命中第 5 行原地更新, 不追加成第二行
    row, how = F.resolve_row(wb["天麓"], "2026-10-12", mode="auto")
    check("auto: 命中天麓第 5 行(而不是认不出→追加)", (row, how) == (5, "existing"), (row, how))
    # ② auto: 新日期 → 追加到第 6 行(上一行 = 第 5 行), 不越界
    row2, how2 = F.resolve_row(wb["天麓"], "2026-10-13", mode="auto")
    check("auto: 新日期追加到第 6 行", (row2, how2) == (6, "appended(after 5)"), (row2, how2))
    check("追加行公式相对平移复制(差异列不空)",
          str(wb["天麓"]["E6"].value) == "=C6*2", wb["天麓"]["E6"].value)
    # ③ 回读定位: 两个日期都找得到, 且不越界
    check("_find_row_by_date: 第 5 行旧日期找得到(不会误判成没写)",
          F._find_row_by_date(wb["天麓"], "2026-10-12") == 5,
          F._find_row_by_date(wb["天麓"], "2026-10-12"))
    check("_find_row_by_date: 新日期 = 第 6 行", F._find_row_by_date(wb["天麓"], "2026-10-13") == 6)
    wb.close()

    # ④ 落盘批量写: 天麓(top 模式)与常规表各写一天, 结果落各自的数据首行
    wb = openpyxl.load_workbook(tpl)
    wb["岚霞路"]["C6"] = "旧值"
    wb.save(tpl)
    wb.close()
    F.fill_many(tpl, [("岚霞路", "2026-10-14", {"C": "长沙晴"}),
                      ("天麓", "2026-10-14", {"C": "湘潭晴"})], mode="top")
    wb3 = openpyxl.load_workbook(tpl)
    check("常规表: 新行插到第 6 行", str(wb3["岚霞路"]["A6"].value)[:10] == "2026-10-14",
          wb3["岚霞路"]["A6"].value)
    check("天麓: 新行插到第 5 行(不写坏表头第 4 行)",
          str(wb3["天麓"]["A5"].value)[:10] == "2026-10-14" and
          wb3["天麓"]["A4"].value == "日期", (wb3["天麓"]["A5"].value, wb3["天麓"]["A4"].value))
    check("天麓: C 列拿到湘潭天气", wb3["天麓"]["C5"].value == "湘潭晴", wb3["天麓"]["C5"].value)
    check("天麓: 旧行(10-12)被正确下推到第 6 行, 内容原样保留",
          str(wb3["天麓"]["A6"].value)[:10] == "2026-10-12" and wb3["天麓"]["C6"].value == "旧值",
          (wb3["天麓"]["A6"].value, wb3["天麓"]["C6"].value))
    check("常规表: 旧行下推到第 7 行(两表各写各的, 没串)",
          str(wb3["岚霞路"]["A7"].value)[:10] == "2026-10-12" and wb3["岚霞路"]["C7"].value == "旧值",
          (wb3["岚霞路"]["A7"].value, wb3["岚霞路"]["C7"].value))
    check("常规表: 新行拿到长沙天气(与天麓的湘潭值不同)",
          wb3["岚霞路"]["C6"].value == "长沙晴", wb3["岚霞路"]["C6"].value)
    wb3.close()


def test_orchestrator_helpers():
    """orchestrator 侧接线: 目标表识别 / 逐店取值 / 逐店时段 / 缺地址提示(全用替身)。"""
    print("【5】orchestrator 逐店接线(目标表 / _wx_of / 营业时段 / 缺地址告警)")
    from merchant_report import orchestrator as O

    d = tempfile.mkdtemp()
    details = mk_details(os.path.join(d, "明细.xlsx"), with_addr=True)
    tpl = os.path.join(d, "模板.xlsx")
    wb = openpyxl.Workbook()
    wb.active.title = "Sheet1"
    wb.create_sheet("Sheet2")
    for sn in ("岚霞路", "高桥"):
        wb.create_sheet(sn)
    wb.save(tpl)
    wb.close()
    store = {"name": "测试商家", "template": tpl, "store_details": details}

    tg = O._weather_targets(store)
    check("_weather_targets 只取明细里配了地址的 Sheet(无地址的行不进 targets)",
          set(tg) == {"岚霞路", "高桥"} and tg["岚霞路"].startswith("湘潭"), tg)
    check("_weather_targets: 未配门店明细 → {}(整条链回退旧口径)",
          O._weather_targets({"name": "x", "template": tpl}) == {})

    by_sheet = {"岚霞路": {"2026-10-13": ("湘潭阴", "18~22℃")},
                "高桥": {"2026-10-13": ("长沙晴", "20~25℃")}}
    wmap = {"2026-10-13": ("长沙晴", "20~25℃")}
    check("_wx_of: 逐店表命中 → 用**该店自己**的值",
          O._wx_of(by_sheet, wmap, "岚霞路", "2026-10-13") == ("湘潭阴", "18~22℃"))
    check("_wx_of: 表里没有 → 回退主表那组(不拿别店顶替)",
          O._wx_of(by_sheet, wmap, "未配置店", "2026-10-13") == ("长沙晴", "20~25℃"))
    check("_wx_of: 逐店表为空 → 旧口径", O._wx_of({}, wmap, "岚霞路", "2026-10-13") == ("长沙晴", "20~25℃"))

    rows = {"2026-10-13": [{"门店名称": "示例（湘潭岚霞路店）", "设置营业时间段": "10:00-22:00"},
                           {"门店名称": "示例（长沙高桥店）", "设置营业时间段": "08:00-20:00"}]}
    biz = O._biz_hours_by_sheet(store, "meituan", rows, ["2026-10-13"],
                                pick=lambda r: r.get("设置营业时间段"))
    check("营业时段逐店逐日(店名 → 明细映射的 Sheet 名)",
          biz.get(("岚霞路", "2026-10-13")) == "10:00-22:00" and
          biz.get(("高桥", "2026-10-13")) == "08:00-20:00", biz)

    class FakeSink:
        def __init__(self):
            self.msgs = []

        def log(self, msg, level="info"):
            self.msgs.append((level, msg))

    sk = FakeSink()
    warned = set()
    O._warn_no_store_weather(sk, "未配置店", tg, warned)
    O._warn_no_store_weather(sk, "未配置店", tg, warned)      # 去重: 第二次不重复刷
    check("缺地址门店写入前给 warn(写明店名, 只提示一次)",
          len(sk.msgs) == 1 and "未配置店" in sk.msgs[0][1] and sk.msgs[0][0] == "warn", sk.msgs)
    sk2 = FakeSink()
    O._warn_no_store_weather(sk2, "未配置店", {}, set())
    check("整份明细都没配地址 → 不刷 warn(老配置旧口径, 不算问题)", sk2.msgs == [])


def main():
    test_details_addr()
    test_city_of_address()
    test_per_store_weather_and_write()
    test_tianlu_structure()
    test_orchestrator_helpers()
    if FAILS:
        print("\n❌ 失败: " + "; ".join(FAILS))
        sys.exit(1)
    print("\n✅ 天气/温度逐店化(T01) + 天麓结构边界 全绿")


if __name__ == "__main__":
    main()
