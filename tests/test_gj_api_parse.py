# -*- coding: utf-8 -*-
"""营业概览「接口直取」解析 + 双读选源单测(不启动浏览器)。

2026-10-11 真机取证(高桥店 10-10): 同一店/日, 接口(BusinessOverviewPrintBusinessCompose)
与页面 innerText 读数**逐项一致**(15/15) → 以此为基线钉住:
  ① 接口解析值与 DOM 版同构同值(渠道集合/三项值/总量);
  ② 交叉校验口径一致(渠道合计 vs 总量);
  ③ 敏感操作(SensOperation)值与详情页一致(真机 times=3 / amt=92);
  ④ 双读选源接线: 一致→用接口; 不一致→以页面为准+告警; 页面没读到→接口顶上;
     解析失败(非 JSON/code!=0/缺块)→回退页面解析。
夹具为真机响应裁剪(保留三个数据块, 双编码原样), 页文本为真机 innerText 片段。
"""
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import gj_mapping as gm                        # noqa: E402
from merchant_report.platforms import meituan_gj as gj                        # noqa: E402

API_JSON = r'''{"msg": "请求成功", "code": 0, "data": {"BusinessEfficiency": {"code": 0, "msg": "请求成功", "data": "{\"employeeCnt\":0,\"businessAreaCnt\":0,\"poiRentAmt\":0,\"employeeEfficiency\":null,\"businessAreaEfficiency\":null,\"poiRentEfficiency\":null,\"payAmt\":372108}"}, "SensOperation": {"code": 0, "msg": "请求成功", "data": "{\"max\":{\"orderOperationTypeEn\":null,\"orderOperationTypeCn\":null,\"orderCntMax\":null,\"skuOperationTypeEn\":\"refundSku\",\"skuOperationTypeCn\":\"退菜\",\"skuCntMax\":3.0,\"orderMaxPoiId\":null,\"orderMaxPoiName\":null,\"orderMaxPoiCnt\":null,\"skuMaxPoiId\":null,\"skuMaxPoiName\":null,\"skuMaxPoiCnt\":null,\"orderOrgCode\":null,\"skuOrgCode\":null},\"orderDetail\":[],\"dishDetail\":[{\"poiId\":null,\"poiName\":null,\"operationTypeEn\":\"refundSku\",\"operationTypeCn\":\"退菜\",\"skuCnt\":3.0,\"skuAmt\":9200,\"sensitiveTimes\":3,\"orgCode\":null}],\"totals\":null}"}, "BusinessOverviewPrintBusinessCompose": {"code": 0, "msg": "请求成功", "data": "{\"sum\":[{\"orderCnt\":253,\"businessAmt\":782010}],\"items\":[{\"serviceAmt\":0,\"discountAmt\":13450,\"orderTypeCode\":1,\"skuSaleAmt\":61150,\"orderCnt\":25,\"incomeAmt\":47700,\"sortWeight\":2,\"orderTypeName\":\"店内销售\",\"businessAmt\":61150,\"foodboxAmt\":0,\"shippingAmt\":0,\"noTaxIncomeAmt\":47700,\"taxAmt\":0,\"discountAmtRatio\":\"22.00%\"},{\"serviceAmt\":0,\"discountAmt\":5360,\"orderTypeCode\":3,\"skuSaleAmt\":15900,\"orderCnt\":7,\"incomeAmt\":10540,\"sortWeight\":0,\"orderTypeName\":\"自提销售\",\"businessAmt\":15900,\"foodboxAmt\":0,\"shippingAmt\":0,\"noTaxIncomeAmt\":10540,\"taxAmt\":0,\"discountAmtRatio\":\"33.71%\"},{\"serviceAmt\":47363,\"discountAmt\":168017,\"orderTypeCode\":101,\"skuSaleAmt\":320840,\"orderCnt\":111,\"incomeAmt\":156323,\"sortWeight\":3,\"orderTypeName\":\"美团外卖\",\"businessAmt\":324340,\"foodboxAmt\":3500,\"shippingAmt\":0,\"noTaxIncomeAmt\":156323,\"taxAmt\":0,\"discountAmtRatio\":\"51.80%\"},{\"serviceAmt\":47164,\"discountAmt\":220105,\"orderTypeCode\":102,\"skuSaleAmt\":368020,\"orderCnt\":108,\"incomeAmt\":155315,\"sortWeight\":2,\"orderTypeName\":\"淘宝闪购\",\"businessAmt\":375420,\"foodboxAmt\":7400,\"shippingAmt\":0,\"noTaxIncomeAmt\":155315,\"taxAmt\":0,\"discountAmtRatio\":\"58.63%\"},{\"serviceAmt\":290,\"discountAmt\":2970,\"orderTypeCode\":108,\"skuSaleAmt\":5200,\"orderCnt\":2,\"incomeAmt\":2230,\"sortWeight\":7,\"orderTypeName\":\"京东秒送\",\"businessAmt\":5200,\"foodboxAmt\":0,\"shippingAmt\":0,\"noTaxIncomeAmt\":2230,\"taxAmt\":0,\"discountAmtRatio\":\"57.12%\"}]}"}}}'''

DOM_TEXT = """报表中心首页
营业概览
图表模式导出取消常用报表说明
营业日期
至
查询重置
查询时间范围：2026/10/10 00:00:00-2026/10/10 23:59:59
营业收款菜品顾客
营业收入(元)
3,721.08
店内销售:477.00
自提销售:105.40
美团外卖:1,563.23
淘宝闪购:1,553.15
京东秒送:22.30
营业额(元)
7,820.10
店内销售:611.50
自提销售:159.00
美团外卖:3,243.40
淘宝闪购:3,754.20
京东秒送:52.00
优惠金额(元)
4,099.02
店内销售:134.50
自提销售:53.60
美团外卖:1,680.17
淘宝闪购:2,201.05
京东秒送:29.70
优惠占比
52.42%
店内销售:22.00%
自提销售:33.71%
美团外卖:51.80%
淘宝闪购:58.63%
京东秒送:57.12%
订单量(单)
253
店内销售:25
自提销售:7
美团外卖:111
淘宝闪购:108
京东秒送:2"""


def test_parse_basic_matches_dom():
    parsed, meta = gm.parse_summary_json(API_JSON)
    assert parsed, meta
    dom, dmeta = gm.scrape_overview_text(DOM_TEXT)
    assert set(parsed) == set(dom), (set(parsed), set(dom))
    for cat in parsed:
        for f in ("营业收入", "营业额", "订单量"):
            assert parsed[cat].get(f) == dom[cat].get(f), (cat, f, parsed[cat], dom[cat])
    assert meta["totals"] == dmeta["totals"], (meta["totals"], dmeta["totals"])
    assert meta["crosscheck_failed"] == [] and dmeta["crosscheck_failed"] == []
    assert meta["zero_data"] is False
    print(f"✅ 接口解析与 DOM 版逐项一致: {sorted(parsed)} | 总量 {meta['totals']}")


def test_sensitive_from_api():
    _, meta = gm.parse_summary_json(API_JSON)
    assert meta["sensitive_from_api"] == {"times": 3.0, "amt": 92.0}, meta["sensitive_from_api"]
    print(f"✅ SensOperation 解析: {meta['sensitive_from_api']}(与详情页真机值一致)")


def test_parse_fail_modes():
    p, m = gm.parse_summary_json("not json at all")
    assert p is None and "api_error" in m, (p, m)
    p, m = gm.parse_summary_json(json.dumps({"code": 74401, "message": "当前服务已到期"}))
    assert p is None and "74401" in str(m.get("api_error")), (p, m)
    obj = json.loads(API_JSON)
    del obj["data"]["BusinessOverviewPrintBusinessCompose"]
    p, m = gm.parse_summary_json(obj)
    assert p is None, (p, m)
    print("✅ 解析失败(非JSON/code!=0/缺块) → (None, api_error), 调用方可回退")


def test_zero_day_variant():
    obj = json.loads(API_JSON)
    d = obj["data"]
    comp = json.loads(d["BusinessOverviewPrintBusinessCompose"]["data"])
    comp["items"] = []
    comp["sum"] = [{"orderCnt": 0, "businessAmt": 0}]
    d["BusinessOverviewPrintBusinessCompose"]["data"] = json.dumps(comp, ensure_ascii=False)
    eff = json.loads(d["BusinessEfficiency"]["data"])
    eff["payAmt"] = 0
    d["BusinessEfficiency"]["data"] = json.dumps(eff, ensure_ascii=False)
    parsed, meta = gm.parse_summary_json(obj)
    assert meta["zero_data"] is True, meta
    assert parsed and all(v == 0 for x in parsed.values() for v in x.values()), parsed
    print("✅ 全零日: 接口版产出全 0 同构结构(zero_data=True)")


def test_readings_equal_gate():
    a, ma = gm.parse_summary_json(API_JSON)
    b, mb = gm.parse_summary_json(API_JSON)
    assert gm.readings_equal(a, ma, b, mb) is True
    bad = json.loads(json.dumps(a))
    bad["美团外卖"]["营业收入"] += 0.02
    assert gm.readings_equal(bad, ma, b, mb) is False
    assert gm.readings_equal(a, ma, {}, {"totals": {}, "zero_data": False}) is False
    print("✅ 双读比对: 一致=True; 差 2 分/缺渠道=False")


def test_date_ms():
    assert gj._gj_date_ms("2026/10/10") == 1791561600000
    assert gj._gj_date_ms("2026-10-09") == 1791475200000
    assert gj._gj_date_ms("") is None
    print("✅ 日期毫秒戳与页面查询请求体同口径")


class _Resp:
    def __init__(self, body, text):
        self.url = "https://pos.meituan.com/web/api/v2/reports/combine/business-summary-page?x=1"
        self.request = types.SimpleNamespace(post_data=body)
        self._t = text

    def text(self):
        return self._t


class _FakePage:
    """最小页面替身: .on/.remove_listener + wait_for_timeout 派发排队的响应。"""

    def __init__(self):
        self.handlers = []
        self.queue = []

    def on(self, ev, h):
        self.handlers.append((ev, h))

    def remove_listener(self, ev, h):
        self.handlers = [(e, x) for e, x in self.handlers if x is not h]

    def wait_for_timeout(self, ms):
        while self.queue:
            r = self.queue.pop(0)
            for ev, h in list(self.handlers):
                if ev == "response":
                    h(r)


def _mk_ad(logs):
    ad = gj.MeituanGjAdapter.__new__(gj.MeituanGjAdapter)
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append(m)})()
    ad._open_biz_overview = lambda page: page
    ad._wait_until = lambda cond, timeout_s=20, what="": True
    ad._last_err = ""
    return ad


def _feed(page, text, body='"startDate":1791561600000'):
    page.queue.append(_Resp(body, text))


def test_adapter_dual_read_consistent():
    logs = []
    ad = _mk_ad(logs)
    page = _FakePage()
    ad._set_gj_dates = lambda p, df, dt: _feed(p, API_JSON)
    ad._text_of = lambda p: DOM_TEXT
    calls = {"n": 0}
    ad._capture_sensitive = lambda p, timeout_s=40: (
        calls.__setitem__("n", calls["n"] + 1) or {"times": 9, "amt": 9})
    data = ad._scrape_one(page, "示例店", "2026/10/10", "2026/10/10")
    assert data, ad._last_err
    assert any("双读一致" in m for m in logs), logs
    assert data["美团外卖"]["营业收入"] == 1563.23, data["美团外卖"]
    assert data.get("_sensitive") == {"times": 3.0, "amt": 92.0}, data.get("_sensitive")
    assert calls["n"] == 0, "接口敏感值可用时不该再进详情页"
    print("✅ 接线: 双读一致 → 用接口值; 敏感走接口(详情页零调用)")


def test_adapter_mismatch_prefers_dom():
    logs = []
    ad = _mk_ad(logs)
    page = _FakePage()
    obj = json.loads(API_JSON)
    comp = json.loads(obj["data"]["BusinessOverviewPrintBusinessCompose"]["data"])
    for it in comp["items"]:
        if it["orderTypeName"] == "美团外卖":
            it["incomeAmt"] = 999999
    obj["data"]["BusinessOverviewPrintBusinessCompose"]["data"] = json.dumps(comp, ensure_ascii=False)
    ad._set_gj_dates = lambda p, df, dt: _feed(p, json.dumps(obj, ensure_ascii=False))
    ad._text_of = lambda p: DOM_TEXT
    calls = {"n": 0}
    ad._capture_sensitive = lambda p, timeout_s=40: (
        calls.__setitem__("n", calls["n"] + 1) or {"times": 5, "amt": 10})
    data = ad._scrape_one(page, "示例店", "2026/10/10", "2026/10/10")
    assert data, ad._last_err
    assert any("不一致" in m and "以页面为准" in m for m in logs), logs
    assert data["美团外卖"]["营业收入"] == 1563.23, data["美团外卖"]
    assert calls["n"] == 1, "不一致时敏感应回退详情页"
    assert data.get("_sensitive") == {"times": 5, "amt": 10}
    print("✅ 接线: 不一致 → 以页面为准+告警; 敏感回退详情页")


def test_adapter_rescue_when_dom_empty():
    logs = []
    ad = _mk_ad(logs)
    page = _FakePage()
    ad._set_gj_dates = lambda p, df, dt: _feed(p, API_JSON)
    ad._text_of = lambda p: ""
    ad._capture_sensitive = lambda p, timeout_s=40: {"times": 1, "amt": 1}
    data = ad._scrape_one(page, "示例店", "2026/10/10", "2026/10/10")
    assert data, ad._last_err
    assert any("采用接口值" in m for m in logs), logs
    assert data["店内销售"]["订单量"] == 25, data["店内销售"]
    print("✅ 接线: 页面没读到 → 接口值顶上(仍过交叉校验)")


def test_adapter_fallback_on_api_fail():
    logs = []
    ad = _mk_ad(logs)
    page = _FakePage()
    ad._set_gj_dates = lambda p, df, dt: _feed(p, '{"code":74401,"message":"服务已到期"}')
    ad._text_of = lambda p: DOM_TEXT
    ad._capture_sensitive = lambda p, timeout_s=40: None
    data = ad._scrape_one(page, "示例店", "2026/10/10", "2026/10/10")
    assert data, ad._last_err
    assert any("回退页面解析" in m for m in logs), logs
    assert data["淘宝闪购"]["营业额"] == 3754.2
    # 没捕获到响应(如页面缓存直接渲染) → 同样回退
    logs2, page2 = [], _FakePage()
    ad2 = _mk_ad(logs2)
    ad2._set_gj_dates = lambda p, df, dt: None
    ad2._text_of = lambda p: DOM_TEXT
    ad2._capture_sensitive = lambda p, timeout_s=40: None
    data2 = ad2._scrape_one(page2, "示例店", "2026/10/10", "2026/10/10")
    assert data2 and any("未命中" in m for m in logs2), logs2
    print("✅ 接线: 接口失败(code=74401) / 未捕获响应 → 均回退页面解析")


def test_contract_guards():
    """契约: 安全闸门不许被静默移除(双读比对 / 不一致以页面为准 / 回退 / 接口敏感直写 / 监听清理)。"""
    import inspect
    src = inspect.getsource(gj.MeituanGjAdapter._scrape_one)
    assert "readings_equal" in src, "双读比对闸门必须保留"
    assert "以页面为准" in src, "不一致时以页面为准的兜底必须保留"
    assert "回退页面解析" in src, "接口失败回退页面解析必须保留"
    assert "sensitive_from_api" in src, "接口敏感值直写(省详情页等待)必须保留"
    assert "remove_listener" in src, "响应监听必须清理(防泄漏)"
    src2 = inspect.getsource(gm.parse_summary_json)
    assert "crosscheck_failed" in src2, "接口版交叉校验口径不许丢"
    assert "zero_data" in src2, "全零日语义不许丢"
    print("✅ 契约: 双读闸门/回退/敏感直写/监听清理/交叉校验 守卫通过")


def main():
    test_parse_basic_matches_dom()
    test_sensitive_from_api()
    test_parse_fail_modes()
    test_zero_day_variant()
    test_readings_equal_gate()
    test_date_ms()
    test_adapter_dual_read_consistent()
    test_adapter_mismatch_prefers_dom()
    test_adapter_rescue_when_dom_empty()
    test_adapter_fallback_on_api_fail()
    test_contract_guards()
    print("✅ 接口取数解析 + 双读接线单测全部通过")


if __name__ == "__main__":
    main()
