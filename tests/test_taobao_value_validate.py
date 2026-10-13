# -*- coding: utf-8 -*-
"""淘宝数值合理性校验单测(T11 收尾 / 规格书 铁律 8, 与任务 B 的美团侧同风格)。

规格书原文:
  · 转化率 ∈ [0, 100]%（出现 >100% 或 `####` 即报错）
  · 人数、订单量、金额 ≥ 0
  · 三态校验: 全部顾客 ≈ 新客 + 老客（允许 ±1 舍入）

纪律(与美团侧一致): **超界值不写该值** + 固定文案告警(可 grep); 三态只提示不拦写入。
"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kernel"))
from merchant_report.platforms import taobao_mapping as tm                          # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


BASE_ALL = {"曝光人数": 1000, "进店人数": 100, "下单人数": 20,
            "新客曝光人数": 700, "新客进店人数": 70, "新客下单人数": 14,
            "老客曝光人数": 300, "老客进店人数": 30, "老客下单人数": 6,
            "营业额": 1000, "收入": 900, "有效订单": 50}


def main():
    print("【1】超界率值 → 不写该值 + 固定文案告警")
    tm.VALIDATE_WARNINGS.clear()
    tm.VALIDATE_NOTES.clear()
    # 率值 150%(>100) → GO 不写; 负数人数 -5 → DP/DV 不写
    bad = dict(BASE_ALL, 商责退单率=150, 进店人数=-5)
    cap_bad = {"外卖_全部顾客": {"shop": {"exp": 1000, "ent": -5, "ord": 20,
                                        "ent_rate": 0.2, "ord_rate": 0.2}}}
    ws = tm.build_write_set(bad, {}, cap_bad)
    check("150% 超界率值被剔除(GO 不写)", ws.get("GO") is None, str(ws.get("GO")))
    check("负值被剔除(DP 不写)", ws.get("DP") is None, str(ws.get("DP")))
    rep = tm.validate_report()
    check("告警含率值超界文案", any("GO=" in x and "[0,100%]" in x for x in rep), str(rep[:4]))
    check("告警含负值文案", any("DP=-5" in x and "负值" in x for x in rep), str(rep[:4]))

    print("【2】正常值不受影响(同一次调用里其它列照常写出)")
    check("正常曝光照写 DM", ws.get("DM") == 1000, str(ws.get("DM")))
    check("正常下单照写 DV", ws.get("DV") == 20, str(ws.get("DV")))

    print("【3】回归: 正常报表 → 无告警, 三态通过记 debug")
    tm.VALIDATE_WARNINGS.clear()
    tm.VALIDATE_NOTES.clear()
    ws2 = tm.build_write_set(BASE_ALL, {}, None)
    check("无告警", tm.validate_report() == [], str(tm.validate_report()))
    check("三态通过 3 组", len(tm.validate_notes()) == 3, str(tm.validate_notes()))
    check("三态文案含 ✓", all("✓" in x for x in tm.validate_notes()), str(tm.validate_notes()))
    check("率值正常写出 DS", ws2.get("DS") == round(100 / 1000, 6), str(ws2.get("DS")))

    print("【4】三态不通过 → warn(不拦写入)")
    tm.VALIDATE_WARNINGS.clear()
    tm.VALIDATE_NOTES.clear()
    off = dict(BASE_ALL, 新客曝光人数=100, 老客曝光人数=100)      # 100+100=200 ≠ 1000
    ws3 = tm.build_write_set(off, {}, None)
    rep3 = tm.validate_report()
    check("三态告警出现", any("三态校验" in x and "曝光人数" in x for x in rep3), str(rep3))
    check("三态不拦写入(EB 照写)", ws3.get("EB") == 100, str(ws3.get("EB")))

    print("【5】边界: 率值恰好 0 / 100 合法")
    tm.VALIDATE_WARNINGS.clear()
    trunc = dict(BASE_ALL, 进店人数=1000, 下单人数=1000)          # 进店转化率 = 100%
    ws4 = tm.build_write_set(trunc, {}, None)
    check("率值=100 合法(DS=1.0)", ws4.get("DS") == 1.0, str(ws4.get("DS")))
    zero = dict(BASE_ALL, 下单人数=0, 新客下单人数=0, 老客下单人数=0)
    ws5 = tm.build_write_set(zero, {}, None)
    check("率值=0 合法(DY=0.0)", ws5.get("DY") == 0.0, str(ws5.get("DY")))

    print("【6】校验接线存在(orchestrator 收尾打印 + 进 summary)")
    src = pathlib.Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kernel",
                                    "merchant_report", "orchestrator.py")).read_text(encoding="utf-8")
    check("收尾打印 validate_report", "_tb_map.validate_report()" in src)
    check("进 summary(数值校验)", '"数值校验"' in src)
    check("容器清零", "_tb_map.VALIDATE_WARNINGS.clear()" in src)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
