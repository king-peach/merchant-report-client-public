# -*- coding: utf-8 -*-
"""美团管家: 渠道行是动态的 —— 有就填真值、没有就填 0; 整页没解析出来不许补 0。

真机依据(2026-10-08, 长沙北辰梅尼超市店 / 2026-10-07 用户截图):
  后台渠道行 = 店内销售 / 自提销售 / 美团外卖 / 淘宝闪购 / 京东秒送  —— **没有抖音外卖**;
  模板却有 HU/HV/HW 三列 → 用户口径: 缺该渠道就写 0(该店抖音外卖确实为 0, 是真实值)。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "kernel"))
from merchant_report.platforms import gj_mapping as g   # noqa: E402


def _mk(cats):
    return {c: {"营业额": 100.0, "营业收入": 80.0, "订单量": 5} for c in cats}


def main():
    # ① 用户截图那个账号: 后台无抖音外卖/自营外卖 → 这两组 6 列写 0, 有渠道的写真值
    p = _mk(["店内销售", "自提销售", "美团外卖", "淘宝闪购", "京东秒送"])
    w = g.build_write_set(p)
    for col in ("HU", "HV", "HW", "HX", "HY", "HZ"):
        assert w.get(col) == 0, f"{col} 缺渠道应写 0, 实得 {w.get(col)!r}"
    assert w.get("HO") == 100.0 and w.get("HQ") == 5, "有该渠道必须写真值"
    print("① 缺渠道 → 0 ✓  (HU/HV/HW=0, HX/HY/HZ=0; 店内 HO=100 HQ=5 ✓)")

    # ② 两个渠道都有 → 模板抖音列 = 抖音 + 自营(用户口径: 汇总到抖音外卖里)
    p2 = _mk(["店内销售", "自提销售", "抖音外卖", "自营外卖"])
    p2["抖音外卖"] = {"营业额": 66.6, "营业收入": 50.0, "订单量": 3}
    w2 = g.build_write_set(p2)
    assert w2.get("HU") == 166.6 and w2.get("HV") == 130.0 and w2.get("HW") == 8, w2
    assert w2.get("HX") == 100.0, "模板自营列仍取后台自营外卖"
    print("② 抖音+自营都有 → 抖音列=两者之和 ✓  (HU=166.6 HV=130.0 HW=8)")

    # ②' 关键场景: 后台**只有自营外卖**、没有抖音行 → 模板抖音列 = 自营的值(不是 0!)
    #    (用户账号就是这么报的: 抖音外卖业务由"自营外卖"渠道报出来)
    p2b = _mk(["店内销售", "自提销售", "自营外卖"])
    p2b["自营外卖"] = {"营业额": 888.0, "营业收入": 700.0, "订单量": 21}
    w2b = g.build_write_set(p2b)
    assert (w2b.get("HU"), w2b.get("HV"), w2b.get("HW")) == (888.0, 700.0, 21), w2b
    print("②' 只有自营无抖音 → 抖音列取自营值 ✓  (HU=888.0 HV=700.0 HW=21)")

    # ③ 渠道存在但字段没解析出来 → 留空(解析问题, 不许用 0 掩盖)
    w3 = g.build_write_set({"店内销售": {"营业收入": 80.0}})
    assert w3.get("HO") is None and w3.get("HQ") is None, f"字段缺失应留空: {w3}"
    assert w3.get("HP") == 80.0
    print("③ 渠道在但字段缺 → 留空 ✓  (HO/HQ=None, HP=80.0 ✓)")

    # ④ 整页一个渠道都没解析出来 → 一列都不许补 0(最危险的错法)
    w4 = g.build_write_set({})
    bad = [k for k, v in w4.items() if v == 0]
    assert not bad, f"整页没解析出来时不许补 0, 却补了: {bad}"
    print("④ 整页解析空 → 不补 0 ✓  (全部留空)")

    # ⑤ 抖音三列别被删掉
    cols = [c for c, _, _ in g.GJ_MAPPING]
    for col in ("HU", "HV", "HW"):
        assert col in cols, f"{col}(抖音外卖) 必须在 GJ_MAPPING 里"
    print(f"⑤ 抖音三列已在映射 ✓  (共 {len(cols)} 列)")
    print("\n✅ 管家动态渠道(有则填值 / 无则填 0 / 解析空不补 0) 全绿")


if __name__ == "__main__":
    main()
