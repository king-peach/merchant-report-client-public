# -*- coding: utf-8 -*-
"""淘宝推广 4 列(GG/GH/GI/GL)写入: 数据来自**单店** capture["promo"]。

2026-10-08 四平台诊断: 推广以前只在**品牌级**抓 → 逐店 vals 拿不到 → 模板 GG/GH/GI/GL 永远空。
修复 = capture_per_store 逐店读推广页(带日期验收)放进 cap["promo"], mapping 照收这 4 列。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "kernel"))
from merchant_report.platforms import taobao as T          # noqa: E402
from merchant_report.platforms.taobao_mapping import build_write_set   # noqa: E402


def _cap(promo=None, dims_ok=False):
    # shop 的 5 个子键在 mapping 里是**不加保护**读的(exp/ent/ent_rate/ord/ord_rate) → 测试必须给全
    _shop = {"exp": None, "ent": None, "ent_rate": None, "ord": None, "ord_rate": None}
    c = {"外卖_全部顾客": {"shop": dict(_shop), "top10": {}}, "爆品团_全部顾客": {"shop": {}, "top10": {}},
         "segments": {}, "customer_dims_ok": dims_ok, "bench": "", "bench_ok": False,
         "ftype_ok": True, "shop_id": "x"}
    if promo is not None:
        c["promo"] = promo
    return c


def main():
    # ① 有 promo → 4 列全写
    v = build_write_set({}, {}, _cap({"GG": 3397.47, "GH": 120000, "GI": 3400, "GL": 500.0}))
    assert (v.get("GG"), v.get("GH"), v.get("GI"), v.get("GL")) == (3397.47, 120000, 3400, 500.0), v
    print("① 有 promo → GG/GH/GI/GL 全写 ✓", {k: v.get(k) for k in ('GG', 'GH', 'GI', 'GL')})

    # ② 没有 promo → 4 列都不写(不臆造)
    v2 = build_write_set({}, {}, _cap(None))
    for k in ("GG", "GH", "GI", "GL"):
        assert v2.get(k) is None, f"没抓到就不该写 {k}={v2.get(k)!r}"
    print("② 无 promo → 4 列留空 ✓")

    # ③ 只有部分字段 → 只写有的
    v3 = build_write_set({}, {}, _cap({"GG": 88.8, "GH": None, "GI": None, "GL": None}))
    assert v3.get("GG") == 88.8 and v3.get("GH") is None and v3.get("GL") is None, v3
    print("③ 只有部分字段 → 写有的, 其余留空 ✓")

    # ④ 源码守卫: 逐店读推广页 + 日期验收 + promo 必须进每店结果
    src = inspect.getsource(T.TaobaoAdapter.capture_per_store)
    assert '"promo": promo' in src, "capture_per_store 必须把 promo 放进每店结果(否则 4 列还是空)"
    assert "_read_promo_store" in src, "要在逐店里调用 _read_promo_store"
    assert "未确认日期" in src, "日期没确认时必须拒绝写入(宁可不写, 不写错)"
    m2 = inspect.getsource(T.TaobaoAdapter._read_promo_store)
    assert "date_ok" in m2 and "总推广消费" in m2 and "充值" in m2, "_read_promo_store 要有日期验收 + 4 指标"
    print("④ 源码守卫: 逐店读推广页 + 日期验收 + promo 进结果 ✓")

    print("\n✅ 淘宝推广 4 列 全绿")


if __name__ == "__main__":
    main()
