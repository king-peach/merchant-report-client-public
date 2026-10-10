# -*- coding: utf-8 -*-
"""美团管家: 敏感操作(IA/IB) + 当日工时(IC) + 线上/线下单量(ID/IE/IF) 接入测试。

真机依据(2026-10-06 长沙绿地之窗店, 2026-10-09 探针复核):
  ① 渠道读数 ↔「订单来源」详情页**逐项相等**: 店内18 = 扫码点餐9+收银POS9;
     自提销售15 = 美团自提在线点15; 美团外卖90/淘宝闪购76/京东秒送10/自营外卖1 全部 1:1;
     两边合计都 = 210 → B 口径(自提在线点算线上) = 线上192 / 线下18,
     直接由渠道读数求和, 不必进「订单来源」详情页(懒加载 XHR, 能不进就不进)。
  ② 敏感操作: 详情页是 canvas + 懒加载, DOM 读不到 → 拦 XHR data.summary **精确键**
     sensitiveTimes=6 / sensitiveAmt=131.4; 同名后缀键 sensitiveTimes_<id>(=操作次数占比
     100.00%)必须忽略, 否则会把百分比当次数写进模板。
  ③ IC 当日工时: 用户口径(2026-10-08)「数据源未定, 先填 0」。
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "kernel"))
from merchant_report.platforms import gj_mapping as gm   # noqa: E402
from merchant_report.platforms import meituan_gj as gj   # noqa: E402

# 绿地之窗 2026-10-06 真机渠道读数(含营业额/营业收入/订单量, 订单量用于 ID/IE)
CH_1006 = {
    "店内销售": {"营业额": 594.5, "营业收入": 533.2, "订单量": 18},
    "自提销售": {"营业额": 275.0, "营业收入": 168.4, "订单量": 15},
    "美团外卖": {"营业额": 2520.9, "营业收入": 1172.67, "订单量": 90},
    "淘宝闪购": {"营业额": 2239.6, "营业收入": 852.5, "订单量": 76},
    "自营外卖": {"营业额": 72.9, "营业收入": 67.0, "订单量": 1},
    "京东秒送": {"营业额": 242.0, "营业收入": 98.0, "订单量": 10},
}

# 敏感操作响应体(按真机结构截取): ①空壳(加载中) ②全值包(含后缀干扰键) ③只有干扰键
SENS_EMPTY = json.dumps({"code": 0, "data": {
    "compareDims": {},
    "fieldMap": {"sensitiveTimes": {"showName": "敏感操作次数"},
                 "sensitiveAmt": {"showName": "敏感操作金额"}},
    "items": [], "pageInfo": {"pageNo": 1, "pageSize": 20, "totalCount": 0},
    "summary": {}}, "message": "成功"})
SENS_VALUES = json.dumps({"code": 0, "data": {
    "items": [{"operateType": "下单后退菜", "orderCnt": {"targetNum": 5},
               "sensitiveAmt": {"targetNum": 131.4}}],
    "summary": {
        "下单后退菜_sensitiveTimes_1731585788068": {"targetNum": 6, "targetStr": "6"},
        "sensitiveTimes_1731651449264": {"targetNum": 1.0, "targetStr": "100.00%"},
        "orderCnt": {"targetNum": 5, "targetStr": "5"},
        "sensitiveTimes": {"targetNum": 6, "targetStr": "6"},
        "sensitiveAmt": {"targetNum": 131.4, "targetStr": "131.40"},
    }}, "message": "成功"})
SENS_ONLY_TRAP = json.dumps({"code": 0, "data": {"summary": {
    "sensitiveTimes_1731651449264": {"targetNum": 1.0, "targetStr": "100.00%"}}},
    "message": "成功"})


def main():
    # ① online_offline: 真机对齐样本 → 线上 192 / 线下 18
    on, off = gm.online_offline(CH_1006)
    assert (on, off) == (192.0, 18.0), (on, off)
    print("① 渠道→线上/线下 ✓  (线上=15+90+76+1+10=192, 线下=18)")

    # ①' 店内销售缺失 → 留空; 全 0 店(闭店) → (0, 0) 照写真实 0
    assert gm.online_offline({"美团外卖": {"订单量": 90}}) == (None, None)
    zero = {c: {"订单量": 0, "营业额": 0, "营业收入": 0} for c in
            ("店内销售", "自提销售", "抖音外卖", "自营外卖")}
    assert gm.online_offline(zero) == (0.0, 0.0)
    print("①' 店内缺失→留空; 全0店→(0,0) ✓")

    # ② _sens_from_bodies: 空壳+全值包混序 → 取全值包; 干扰键/半包/非JSON → None
    got = gj._sens_from_bodies([SENS_EMPTY, SENS_VALUES])
    assert got == {"times": 6.0, "amt": 131.4}, got
    assert gj._sens_from_bodies([SENS_EMPTY]) is None
    assert gj._sens_from_bodies([SENS_ONLY_TRAP]) is None, "后缀键(占比)不许当次数!"
    assert gj._sens_from_bodies(["不是json", ""]) is None
    print("② 敏感操作解析 ✓  (times=6 / amt=131.4; 空壳、干扰键、垃圾→None)")

    # ③ build_write_set: 六列 IA/IB/IC/ID/IE/IF
    p = dict(CH_1006)
    p["_sensitive"] = {"times": 6.0, "amt": 131.4}
    w = gm.build_write_set(p)
    assert (w["IA"], w["IB"]) == (6.0, 131.4), w
    assert w["IC"] == 0, w
    assert (w["ID"], w["IE"], w["IF"]) == (192.0, 18.0, 210.0), w
    print("③ write_set ✓  (IA=6 IB=131.4 IC=0 ID=192 IE=18 IF=210)")

    # ③' 无 _sensitive → IA/IB 留空; ID/IE/IC 照写
    w2 = gm.build_write_set(dict(CH_1006))
    assert "IA" not in w2 and "IB" not in w2, w2
    assert (w2["ID"], w2["IE"], w2["IC"]) == (192.0, 18.0, 0), w2
    # ③'' 整页解析空 → 连 IC/ID 都不写(最危险的错法: 把"没抓到"写成"全0")
    w3 = gm.build_write_set({})
    assert not any(k in w3 for k in ("IA", "IB", "IC", "ID", "IE", "IF")), w3
    print("③' 无敏感数据→IA/IB留空; 解析空→六列全不写 ✓")

    # ④ _scrape_one 接线: 抓到 → 并入 parsed["_sensitive"]; 抓不到 → 照常返回不写
    real_parse = gm.scrape_overview_text
    gm.scrape_overview_text = lambda txt: (dict(CH_1006), {
        "totals": {}, "crosscheck_failed": [], "zero_data": False,
        "unknown_channels": {}, "no_data_hint": False})
    try:
        for sens_ret, has in (({"times": 6.0, "amt": 131.4}, True), (None, False)):
            ad = gj.MeituanGjAdapter.__new__(gj.MeituanGjAdapter)
            logs = []
            ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append(m)})()
            ad._open_biz_overview = lambda page: page
            ad._set_gj_dates = lambda page, df, dt: None
            ad._wait_until = lambda cond, timeout_s=20, what="": True
            ad._text_of = lambda page: "营业收入"
            ad._last_err = ""
            ad._capture_sensitive = lambda page, timeout_s=40: sens_ret
            got = ad._scrape_one(object(), "长沙绿地之窗店", "2026-10-06", "2026-10-06")
            assert got, ad._last_err
            ws = gm.build_write_set(got)
            if has:
                assert got.get("_sensitive") == {"times": 6.0, "amt": 131.4}, got.get("_sensitive")
                assert (ws["IA"], ws["IB"]) == (6.0, 131.4), ws
            else:
                assert "_sensitive" not in got, got
                assert "IA" not in ws and "IB" not in ws, ws
            # 两种情况 ID/IE/IC 都必须照写(敏感操作抓不到 ≠ 本店其它列不写)
            assert (ws["ID"], ws["IE"], ws["IF"], ws["IC"]) == (192.0, 18.0, 210.0, 0), ws
    finally:
        gm.scrape_overview_text = real_parse
    print("④ _scrape_one 接线 ✓  (抓到并入 _sensitive / 抓不到留空, ID/IE/IC 不受影响)")

    print("\n✅ 管家 敏感操作·当日工时·线上线下单量 接入测试全绿")


if __name__ == "__main__":
    main()
