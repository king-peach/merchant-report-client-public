# -*- coding: utf-8 -*-
"""管家「当天无数据」vs「页面结构变了」的区分测试(用真机抓到的页面文本)。

2026-09-28 真机: 23 店里 6 家(坡子街/浏城桥/天马公寓/杜甫江阁/天麓尚层/兰亭湾畔)平台
返回全 0 + 各构成块「暂无数据」, 渠道明细一行没有 → 旧代码报「页面结构可能已更新」,
把人引去查 DOM(白费功夫)。真因是这些店当天没营业数据。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import gj_mapping as gm                        # noqa: E402

# 真机抓取(折叠了顶部刷屏的品牌名): 无数据店
ZERO_TXT = """品牌商-张泽来 01459 报表中心首页 营业概览 营业日期 至 查询重置
查询时间范围：2026/09/27 00:00:00-2026/09/27 23:59:59 营业收款菜品顾客
营业收入(元) 0.00 营业额(元) 0.00 优惠金额(元) 0.00 优惠占比 0 订单量(单) 0
营业构成 订单分类构成 详情 暂无数据 销售品项构成 详情 暂无数据
优惠构成 详情 暂无数据 营业收入构成 详情 暂无数据 部门营业排行 详情 暂无数据
综合统计 敏感操作 详情 暂无数据 餐段分布 详情 暂无数据 订单来源分布 详情 暂无数据
消费指标 详情 折前单均(元) 0.00 折后单均(元) 0.00 用餐人数 0"""

# 真机抓取: 正常店(麻园湾 2026-09-27)
GOOD_TXT = """报表中心首页 营业概览 营业日期 至 查询重置
查询时间范围：2026/09/27 00:00:00-2026/09/27 23:59:59 营业收款菜品顾客
营业收入(元) 2,925.57 店内销售:1,520.33 自提销售:278.30 美团外卖:394.78 淘宝闪购:720.86 京东秒送:11.30
营业额(元) 4,998.10 店内销售:1,898.70 自提销售:452.00 美团外卖:745.20 淘宝闪购:1,873.20 京东秒送:29.00
优惠金额(元) 2,072.53 店内销售:378.37 自提销售:173.70 美团外卖:350.42 淘宝闪购:1,152.34 京东秒送:17.70
优惠占比 41.47% 店内销售:19.93% 自提销售:38.43% 美团外卖:47.02% 淘宝闪购:61.52% 京东秒送:61.03%
订单量(单) 176 店内销售:67 自提销售:21 美团外卖:37 淘宝闪购:50 京东秒送:1
营业构成 订单分类构成 详情 营业收入 营业收入(元) 2,925.57"""

# 结构真的变了(标签都没了)
BROKEN_TXT = "报表中心 营业概览 营业日期 至 查询 查询时间范围：2026/09/27 一些陌生的新文案"


def main():
    # ① 无数据店: 「能拿到数据但数值全 0」= 真实数据 → 要产出**同构的全 0 结构**(供照写进模板),
    #    而不是空结构(空结构会被下游整店跳过, 模板里缺这家店)
    out, meta = gm.scrape_overview_text(ZERO_TXT)
    assert meta["zero_data"] is True, meta
    assert meta["no_data_hint"] is True, meta
    assert meta["totals"]["营业收入"] == 0.0, meta
    assert out, "全 0 店现在必须产出结构(否则下游会整店跳过)"
    ws = gm.build_write_set(out)
    assert ws, ws
    bad = {k: v for k, v in ws.items() if v not in (None, 0)}
    assert not bad, f"全 0 店不该写出非 0 值: {bad}"
    n_zero = sum(1 for v in ws.values() if v == 0)
    assert n_zero > 0, "应把 0 写进模板列"
    print(f"✅ 无数据店: 产出全 0 结构 → 照写模板({n_zero} 列写 0), zero_data=True")

    # ② 正常店: 五个渠道都在, 不误标 zero_data
    out, meta = gm.scrape_overview_text(GOOD_TXT)
    assert set(out) == {"店内销售", "自提销售", "美团外卖", "淘宝闪购", "京东秒送"}, out
    assert out["店内销售"]["营业收入"] == 1520.33, out["店内销售"]
    assert out["京东秒送"]["订单量"] == 1, out["京东秒送"]
    assert meta["zero_data"] is False, meta
    assert meta["totals"]["营业收入"] == 2925.57, meta
    print(f"✅ 正常店: 5 渠道齐全(店内销售 收入{out['店内销售']['营业收入']}/单{out['店内销售']['订单量']}), zero_data=False")

    # ③ 结构真变了: 不能误判成 zero_data(否则又会漏报真实的改版)
    out, meta = gm.scrape_overview_text(BROKEN_TXT)
    assert out == {}, out
    assert meta["zero_data"] is False, meta
    print("✅ 结构真变了: zero_data=False(仍按「页面结构可能已更新」报, 不会掩盖改版)")

    # ④ 无数据店: 「能拿到数据但全 0」= 真实值 → 返回全 0 结构照写; 且不许误报"结构已更新"
    from merchant_report.platforms import meituan_gj as gj
    ad = gj.MeituanGjAdapter.__new__(gj.MeituanGjAdapter)
    logs = []
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append(m)})()
    ad._open_biz_overview = lambda page: page
    ad._set_gj_dates = lambda page, df, dt: None
    ad._wait_until = lambda cond, timeout_s=20, what="": True
    ad._text_of = lambda page: ZERO_TXT
    ad._last_err = ""
    got = ad._scrape_one(object(), "长沙坡子街店", "2026-09-27", "2026-09-27")
    assert got, "全 0 店现在必须返回结构(照写), 不再是 None"
    wsv = gm.build_write_set(got)
    assert wsv and all(v in (None, 0) for v in wsv.values()), wsv
    assert not any("结构" in m for m in logs), logs
    print(f"✅ 全 0 店: 返回结构并照写({sum(1 for v in wsv.values() if v == 0)} 列写 0), 未误报结构变更")

    # ⑤ 结构变化时仍报结构问题
    ad._text_of = lambda page: BROKEN_TXT
    assert ad._scrape_one(object(), "某店", "2026-09-27", "2026-09-27") is None
    assert "结构可能已更新" in ad._last_err, ad._last_err
    print("✅ 结构变化仍报「页面结构可能已更新」")

    print("✅ 管家「无数据 vs 结构变化」区分测试全部通过")


if __name__ == "__main__":
    main()
