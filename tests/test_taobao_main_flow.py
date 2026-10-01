# -*- coding: utf-8 -*-
"""淘宝「主站本店流量」以前只靠网页抓取 → 门店 Sheet 全空(2026-09-30 用户报)。

铁证(今天真机数据): 全部报表 = 主站 + 爆品团
    下单人数 892(全部合计) − 137(爆品团合计) = 755 = 流量页「外卖」口径账号级值 ✓ 完全一致
    曝光 74,383−26,302 = 48,081 (网页 50,660, 差 5.4%) / 进店 3,045−475 = 2,570 (网页 2,654, 差 3.2%)
    → 差异来自平台跨业务去重; 口径 = 先用报表减法(逐店), 网页整体值有值时覆盖。

同时修掉两个网页抓取的错(2026-09-30 用报表数字反推核实):
  ① 漏斗第二列是「顾客维度=新客」不是「商圈同基准」(64,454 = Σ新客曝光, 三项全等)
     → 旧代码把新客数写进了「商圈TOP」列 = 口径错; 现在 TOP 列**不写**。
  ② 新客/老客列口径是**转化率**, 旧代码写人数; 且顾客下拉点不开时三态相同仍照写。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import taobao_mapping as tm                            # noqa: E402

ALL = {"曝光人数": 4414, "进店人数": 180, "下单人数": 55,
       "新客曝光人数": 3000, "新客进店人数": 130, "新客下单人数": 40,
       "老客曝光人数": 1414, "老客进店人数": 50, "老客下单人数": 15,
       "营业额": 1000, "收入": 900, "有效订单": 50}
BOM = {"曝光人数": 2541, "进店人数": 65, "下单人数": 15,
       "新客曝光人数": 1800, "新客进店人数": 45, "新客下单人数": 10,
       "老客曝光人数": 741, "老客进店人数": 20, "老客下单人数": 5}
MAIN = ("DN", "DQ", "DT", "DW", "DZ", "EC", "EF", "EI", "EL", "EO", "ER")
TOP_COLS = ("DO", "DR", "DU", "DX", "EA", "ED", "EG", "EJ", "EM", "EP", "ES",
            "FA", "FD", "FG", "FJ", "FM", "FP", "FS", "FV", "FY", "GB", "GE")


def _sh(exp, ent, ord_):
    return {"exp": exp, "ent": ent, "ord": ord_,
            "ent_rate": round(ent / exp, 6) if exp else None,
            "ord_rate": round(ord_ / ent, 6) if ent else None}


def test_main_flow_from_reports_without_capture():
    """逐店写入(门店 Sheet)不传网页抓取 → 主站 11 列必须由报表减法给出。"""
    ws = tm.build_write_set(ALL, BOM, None)
    assert ws["DN"] == 1873 and ws["DQ"] == 115 and ws["DW"] == 40, {k: ws[k] for k in ("DN", "DQ", "DW")}
    assert ws["DT"] == round(115 / 1873, 6) and ws["DZ"] == round(40 / 115, 6)
    assert ws["EC"] == 1200 and ws["EL"] == 673
    assert ws["EF"] == round(85 / 1200, 6) and ws["EI"] == round(30 / 85, 6)
    assert ws["EO"] == round(30 / 673, 6) and ws["ER"] == round(10 / 30, 6)
    assert all(ws.get(k) is not None for k in MAIN), {k: ws.get(k) for k in MAIN}
    assert not any(ws.get(k) for k in TOP_COLS), "TOP 列不该有值(页面取不到商圈同行)"
    print("✅ 不依赖网页: 主站流量 11 列由「全部−爆品团」逐店算出(含转化率), TOP 列留空")


def test_perstore_capture_writes_top10():
    """单店逐店抓取(2026-10-01 真机结构) → 主站整体列 + 商圈TOP列都写; 爆品团 TOP 来自爆品团那次读。

    真机样例(铁道学院店 2026-09-29): 外卖本店 8,059/365/107(全部口径含爆品团, 这里只验装配);
    外卖前10%基准 6,043/565/124; 爆品团前10%基准 731/32/8。
    """
    cap = {
        "外卖_全部顾客": {"shop": _sh(3409, 158, 45),
                        "top10": _sh(5231, 547, 110)},
        "爆品团_全部顾客": {"shop": {}, "top10": _sh(731, 32, 8)},
        "customer_dims_ok": False, "bench": "商圈同行前10%均值", "bench_ok": True,
        "ftype_ok": True, "shop_id": "555555555",
    }
    ws = tm.build_write_set(ALL, BOM, cap)
    assert ws["DN"] == 3409 and ws["DQ"] == 158 and ws["DW"] == 45, (ws["DN"], ws["DQ"], ws["DW"])
    assert ws["DT"] == round(158 / 3409, 6) and ws["DZ"] == round(45 / 158, 6)
    assert ws["DO"] == 5231 and ws["DR"] == 547 and ws["DX"] == 110, (ws["DO"], ws["DR"], ws["DX"])
    assert ws["DU"] == round(547 / 5231, 6) and ws["EA"] == round(110 / 547, 6)
    assert ws["FA"] == 731 and ws["FD"] == 32 and ws["FJ"] == 8, (ws["FA"], ws["FD"], ws["FJ"])
    assert ws["FG"] == round(32 / 731, 6) and ws["FM"] == round(8 / 32, 6)
    # 新客/老客没抓(单店页本次没取顾客维度) → 保持报表逐店口径
    assert ws["EC"] == 1200 and ws["EF"] == round(85 / 1200, 6)
    print("✅ 逐店 capture: 主站整体列 + 商圈TOP(前10%)列齐全, 新客/老客回退报表口径")


def test_bench_not_top10_leaves_top_empty():
    """基准没切成前10%(top10 为空) → TOP 列必须留空(宁可不写, 也不写错)。"""
    cap = {"外卖_全部顾客": {"shop": _sh(3409, 158, 45), "top10": {}},
           "爆品团_全部顾客": {"shop": {}, "top10": {}},
           "customer_dims_ok": False, "bench": "商圈同行均值", "bench_ok": False, "ftype_ok": True}
    ws = tm.build_write_set(ALL, BOM, cap)
    assert ws["DN"] == 3409, "本店列仍可写"
    assert not any(ws.get(k) is not None for k in TOP_COLS), \
        f"TOP列不该有值: {[(k, ws.get(k)) for k in TOP_COLS if ws.get(k) is not None]}"
    print("✅ 基准非前10%: TOP列留空(只写本店列)")


def test_dim_not_switched_falls_back_to_reports():
    """顾客维度没生效(新客=老客) → dims_ok=False → 新客/老客必须回退报表口径。"""
    same = _sh(50660, 2654, 755)
    cap = {f"外卖_{c}": {"shop": dict(same), "top10": {}} for c in ("全部顾客", "新客", "老客")}
    cap.update({f"爆品团_{c}": {"shop": dict(same), "top10": {}} for c in ("全部顾客", "新客", "老客")})
    cap["customer_dims_ok"] = False
    ws = tm.build_write_set(ALL, BOM, cap)
    assert ws["EC"] == 1200 and ws["EF"] == round(85 / 1200, 6), (ws["EC"], ws["EF"])
    assert ws["EL"] == 673 and ws["ER"] == round(10 / 30, 6), (ws["EL"], ws["ER"])
    assert ws["DN"] == 50660, "整体三列仍可用网页值(与顾客维度无关)"
    assert not any(ws.get(k) for k in TOP_COLS)
    print("✅ 顾客维度没生效: 新客/老客回退报表口径(不把整体值当新客写)")


def test_missing_bom_row_and_zero_rates():
    """爆品团报表里没这家店(17 vs 16) → 按 0 算而不是整店不写; 分子为 0 的率要写 0 而不是空。

    真机场景: 兰亭湾畔店只出现在「全部」报表(17 vs 16), 下单=0(报表写 '0') → 旧代码直接崩。
    """
    only_all = {"曝光人数": 14, "进店人数": 1, "下单人数": "0", "新客曝光人数": 5, "老客曝光人数": 9,
                "新客进店人数": "1", "老客进店人数": "0", "新客下单人数": "0", "老客下单人数": "0"}
    ws = tm.build_write_set(only_all, None, None)          # bom_row=None 不再崩
    assert ws["DN"] == 14 and ws["DQ"] == 1 and ws["DW"] == 0, (ws["DN"], ws["DQ"], ws["DW"])
    assert ws["DZ"] == 0.0, f"下单=0 应写 0(不是空): {ws['DZ']}"
    assert ws["EC"] == 5 and ws["EL"] == 9
    assert ws["EF"] == round(1 / 5, 6) and ws["EI"] == 0.0, (ws["EF"], ws["EI"])   # 1/5 ; 0/1=0
    assert ws["EO"] == 0.0 and ws["ER"] is None, (ws["EO"], ws["ER"])              # 0/9=0 ; 0/0 不写
    assert ws["DT"] == round(1 / 14, 6)
    print("✅ 爆品团缺店按 0 算(不整店丢), 分子为 0 的转化率写 0")


def test_real_report_files_if_present():
    """用今天真机报表核对: 岚霞路 全部−爆品团 与文件里的数字对得上。"""
    d = pathlib.Path.home() / "Downloads/商家报表整理"
    a = sorted(d.glob("门店下载_20260929*233528620.xlsx"))
    b = sorted(d.glob("门店下载_20260929*233534806.xlsx"))
    if not (a and b):
        print("… 真机报表不在, 跳过")
        return
    from merchant_report import fill
    rows_all = fill.load_report_rows_by_store(str(a[0]))["2026-09-29"]
    rows_bom = fill.load_report_rows_by_store(str(b[0]))["2026-09-29"]
    name = "示例柠檬茶(湘潭岚霞路店)"
    ra = next((r for r in rows_all if r.get("门店名称") == name), None)
    rb = next((r for r in rows_bom if r.get("门店名称") == name), None)
    if not (ra and rb):
        # 公开脱敏版: 真机报表里的门店名对不上测试里的字面量 → 优雅跳过(私有仓会跑)
        print("… 报表里没有该门店(公开版门店名已脱敏) → 跳过真机核对")
        return
    ws = tm.build_write_set(ra, rb, None)
    exp = float(ra["曝光人数"]) - float(rb["曝光人数"])
    ent = float(ra["进店人数"]) - float(rb["进店人数"])
    assert ws["DN"] == round(exp, 4) and ws["DQ"] == round(ent, 4), (ws["DN"], ws["DQ"], exp, ent)
    assert ws["DT"] == round(ent / exp, 6), ws["DT"]
    print(f"✅ 真机核对({name}): 主站曝光 {ws['DN']:.0f} = 全部 {ra['曝光人数']} − 爆品团 {rb['曝光人数']}")


def main():
    for fn in (test_main_flow_from_reports_without_capture, test_perstore_capture_writes_top10,
               test_bench_not_top10_leaves_top_empty, test_dim_not_switched_falls_back_to_reports,
               test_missing_bom_row_and_zero_rates, test_real_report_files_if_present):
        fn()
    print("✅ 淘宝主站流量(报表逐店口径)+新客老客率列+TOP列留空 单测全部通过")


if __name__ == "__main__":
    main()
