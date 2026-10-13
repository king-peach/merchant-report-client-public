# -*- coding: utf-8 -*-
"""京东「取文件」规则单测(规格书 铁律 10 / §4.8)。

需求原文:
    "下载列表累积历史且按操作时间**倒序**，必须取「**文件名日期区间 = 目标日**
     且 **操作时间最新**」的一份。**禁止盲取列表第一条。**"

真机背景: 下载列表累积历史(示例页 32 条), 同名区间可能有多份(重跑/重复提交),
且**列表顺序不一定等于操作时间序**(筛选/分页后更不可靠) → 必须**显式解析每一行**
的文件名/操作时间, 按时间取最新, 而不是拿列表第一条。
"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kernel"))
from merchant_report.platforms import jd as J                                    # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def row(name, time, has_dl=True):
    return {"name": name, "time": time, "has_dl": has_dl, "text": f"{name} {time} 成功 下载"}


def main():
    print("【1】文件名区间解析: 只认 门店_{起8}_{止8}_ 前缀")
    check("目标日命中", J.jd_interval_matches("门店_20260919_20260919_demo_account_2026-10-13 21_02_34.xlsx",
                                              "2026-09-19", "2026-09-19"))
    check("区间不符 → 不算", not J.jd_interval_matches(
        "门店_20260917_20260922_demo_account_2026-10-13 21_02_34.xlsx", "2026-09-19", "2026-09-19"))
    check("非京东命名 → 不算", not J.jd_interval_matches("门店下载_20260919至20260919_全部门店_x.xlsx",
                                                         "2026-09-19", "2026-09-19"))
    check("起止都要对: 文件是单日区间、请求跨天 → 不算", not J.jd_interval_matches(
        "门店_20260919_20260919_a_x.xlsx", "2026-09-18", "2026-09-19"))

    print("【2】操作时间解析")
    check("带空格冒号", J.parse_op_time("2026-10-13 21:02:34") == (2026, 10, 13, 21, 2, 34))
    check("下划线形态", J.parse_op_time("2026-10-13 21_02_34") == (2026, 10, 13, 21, 2, 34))
    check("斜杠 + 无秒", J.parse_op_time("2026/10/13 21:02") == (2026, 10, 13, 21, 2, 0))
    check("解析不出 → None", J.parse_op_time("成功") is None)

    print("【3】多候选 + 列表倒序 → 必须选中操作时间最新的那份(而不是第一条)")
    # 列表首条是**更早**下载的(列表顺序不可信), 最新那份在第 3 条
    rows = [
        row("门店_20260919_20260919_demo_account_2026-10-11 09_00_00.xlsx", "2026-10-11 09:00:00"),
        row("门店_20260917_20260922_demo_account_2026-10-12 08_00_00.xlsx", "2026-10-12 08:00:00"),
        row("门店_20260919_20260919_demo_account_2026-10-13 21_02_34.xlsx", "2026-10-13 21:02:34"),
        row("门店_20260919_20260919_demo_account_2026-10-12 15_10_00.xlsx", "2026-10-12 15:10:00"),
    ]
    best, ordered, why = J.pick_latest_candidate(rows, "2026-09-19", "2026-09-19")
    check("选中最新那份", best and best["name"].endswith("2026-10-13 21_02_34.xlsx"), str(best))
    check("候选只含目标区间(排除 09-17~09-22 那份)", len(ordered) == 3, str(len(ordered)))
    check("选取理由含时间", "最新" in why and "2026-10-13 21:02:34" in why, why)

    print("【4】重复区间(同一区间多份) → 取时间最新; 时间完全相同 → 取列表中靠前的")
    dup = [
        row("门店_20260919_20260919_a_2026-10-13 10_00_00.xlsx", "2026-10-13 10:00:00"),
        row("门店_20260919_20260919_a_2026-10-13 10_00_00.xlsx", "2026-10-13 10:00:00"),
        row("门店_20260919_20260919_a_2026-10-13 09_00_00.xlsx", "2026-10-13 09:00:00"),
    ]
    b2, _, w2 = J.pick_latest_candidate(dup, "2026-09-19", "2026-09-19")
    check("同时间取靠前那条", b2["_ord"] == 0, str(b2))
    check("理由提到同时间", "同时间" in w2, w2)

    print("【5】无候选 → 报错信号(None + 说明), 由调用方 raise")
    b3, o3, w3 = J.pick_latest_candidate([row("门店_20260101_20260101_a_2026-01-02 00_00_00.xlsx",
                                              "2026-01-02 00:00:00")],
                                         "2026-09-19", "2026-09-19")
    check("无候选返回 None", b3 is None and o3 == [], f"{b3} {o3}")
    check("说明=无候选", w3 == "无候选", w3)
    check("空列表也不崩", J.pick_latest_candidate([], "2026-09-19", "2026-09-19")[0] is None)

    print("【6】操作时间解析不出 → 退回列表原序(平台按操作时间倒序, 原序第一条最可靠)")
    noTime = [row("门店_20260919_20260919_a_x.xlsx", "成功"),
              row("门店_20260919_20260919_b_x.xlsx", "成功")]
    b4, _, w4 = J.pick_latest_candidate(noTime, "2026-09-19", "2026-09-19")
    check("退回原序第一条", b4["name"].endswith("_a_x.xlsx"), str(b4))
    check("理由说明退回原序", "原序" in w4, w4)

    print("【7】下载到错误文件必须报错(JDWrongFile)")
    check("异常类存在", issubclass(J.JDWrongFile, RuntimeError))
    src = pathlib.Path(J.__file__).read_text(encoding="utf-8")
    check("落盘前校验 suggested_filename", "suggested_filename" in src and "JDWrongFile" in src)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
