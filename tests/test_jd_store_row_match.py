# -*- coding: utf-8 -*-
"""京东门店行匹配 + 行内自校验 + 取值写入 单测(规格书 铁律 11 / §4.8)。

真机坑(规格书点名):
  · 门店名含**间隔号「·」**: `示例·柠檬茶(长沙高桥店)`, 漏「·」一行都匹配不到;
    括号为**半角** `()`; 全角/半角混用也要能对上 → 标准化时**保留「·」**、全角括号转半角。
  · 门店匹配**优先按「门店id」**(高桥店=98765432);
  · 行内自校验 `收入 = 营业额 − 支出`(±0.01) —— 不成立极可能取错列, 该行不写 + 打印原始值。
验收基准(高桥店 2026-09-19): 营业额 258 / 收入 97.51 / 支出 160.49 / 有效订单 10。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kernel"))

from merchant_report import orchestrator as O                                    # noqa: E402
from merchant_report.platforms import jd_mapping as JM                           # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# 高桥店真实行(与 refs/samples 京东_门店_20261006_20261006.xlsx 同形状)
GAOQIAO = {"门店名称": "示例·柠檬茶(长沙高桥店)", "门店id": 98765432.0, "日期": "2026-09-19",
           "收入": 97.51, "营业额": 258.0, "支出": 160.49, "有效订单": 10.0}
OTHER = {"门店名称": "示例·柠檬茶(长沙铁道学院店)", "门店id": 88888888.0, "日期": "2026-09-19",
         "收入": 195.56, "营业额": 426.7, "支出": 231.14, "有效订单": 18.0}
ROWS = [OTHER, GAOQIAO]


def main():
    print("【1】名称标准化: 全角括号→半角, **保留「·」**")
    check("全角(→半角匹配",
          O._norm_store_name("示例·柠檬茶（长沙高桥店）") == O._norm_store_name("示例·柠檬茶(长沙高桥店)"))
    check("去空格", O._norm_store_name(" 示例·柠檬茶 (长沙高桥店) ")
          == O._norm_store_name("示例·柠檬茶(长沙高桥店)"))
    check("「·」必须保留(norm 后仍含 ·)", "·" in O._norm_store_name("示例·柠檬茶(长沙高桥店)"))
    # 对照: sheet_match.norm 会把 · 一起剥掉 → 直接套用就是本次要防的坑
    from merchant_report import sheet_match
    check("sheet_match.norm 会剥掉·(所以不能直接套用)", "·" not in sheet_match.norm("示例·柠檬茶(长沙高桥店)"))

    print("【2】匹配优先级: 门店id 精确 > 名称标准化精确")
    r, why = O._match_store_row(ROWS, "随便写的名字", store_id="98765432")
    check("编号优先(名称不参与也命中)", r is GAOQIAO and "编号锁定" in why, f"{r} {why}")
    r2, why2 = O._match_store_row(ROWS, "示例·柠檬茶（长沙高桥店）", store_id="999")
    check("编号不中 → 名称兜底(全角括号可匹配)", r2 is GAOQIAO and "名称" in why2, f"{r2} {why2}")

    print("【3】漏「·」→ 匹配不到并给出告警原因(规格书警告的坑)")
    r3, why3 = O._match_store_row(ROWS, "示例柠檬茶(长沙高桥店)")
    check("漏·匹配不到", r3 is None, str(r3))
    check("原因提到间隔号「·」", "·" in why3, why3)

    print("【4】多候选(同归一化名多行)→ 不猜, 告警跳过")
    dup = [dict(GAOQIAO), {"门店名称": "示例·柠檬茶（长沙高桥店）", "门店id": 1, "日期": "2026-09-19"}]
    r4, why4 = O._match_store_row(dup, "示例·柠檬茶(长沙高桥店)")
    check("多候选返回 None", r4 is None and "命中 2 行" in why4, f"{r4} {why4}")

    print("【5】行内自校验: 收入 ≈ 营业额 − 支出(±0.01)")
    JM.IDENTITY_WARNINGS.clear()
    check("高桥行通过", JM.check_identity(GAOQIAO) is True)
    check("通过时无告警", JM.IDENTITY_WARNINGS == [], str(JM.IDENTITY_WARNINGS))
    bad = dict(GAOQIAO)
    bad["收入"] = 258.0          # 把"营业额"当"收入"(典型取错列)
    check("错列行不通过", JM.check_identity(bad) is False)
    check("告警含原始值(门店名/门店id/日期/收入/营业额/支出/有效订单)",
          all(k in JM.IDENTITY_WARNINGS[-1] for k in
              ("门店名称", "门店id", "日期", "收入", "营业额", "支出", "有效订单")),
          JM.IDENTITY_WARNINGS[-1])
    check("字段不全 → 不判定(不误伤)", JM.check_identity({"收入": 1.0}) is None)

    print("【6】build_write_set: 通过 → HH/HI/HJ 按硬编码映射; 不通过 → 该行不写")
    ws = JM.build_write_set(GAOQIAO)
    check("HH=营业额 258", ws.get("HH") == 258.0, str(ws.get("HH")))
    check("HI=收入 97.51", ws.get("HI") == 97.51, str(ws.get("HI")))
    check("HJ=有效订单 10", ws.get("HJ") == 10.0, str(ws.get("HJ")))
    ws_bad = JM.build_write_set(bad)
    check("错列行三列全不写", all(ws_bad.get(c) is None for c in ("HH", "HI", "HJ")), str(ws_bad))
    check("天气列不受牵连(仍照写 C)", JM.build_write_set(bad, weather=("多云", 22)).get("C") == "多云")

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
