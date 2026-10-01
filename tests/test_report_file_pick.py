# -*- coding: utf-8 -*-
"""报表选文件回归测试: 文件名末段是**下载时间戳**不是报表日期。

真坑(2026-09-24 用户实测): 请求 09-23, 目录里有
  门店下载_20260917至20260922_全部门店_1111111111_20260923164541.xlsx  ← 名字含 20260923
                                                                        (其实是 09-23 下载的)
内容却只有 09-17~09-22 → 旧逻辑按名字把它当成 09-23 的「全部」报表 → 报
「报表内缺少这些日期的数据行」。本测试锁死: **必须以内容为准**。
"""
import os
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
import openpyxl                                                             # noqa: E402
from merchant_report.fill import (canonical_report_name, dates_span_text,    # noqa: E402
                                  find_report_files, load_report_rows,
                                  parse_canonical_name, report_file_candidates,
                                  report_kind)

ALL_H = ["日期", "门店编号", "门店名称", "满减活动订单数", "营业额"]
BOM_H = ["日期", "门店编号", "门店名称", "曝光人数", "进店人数"]


def mk(path, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(headers)
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path


def main():
    D = tempfile.mkdtemp(prefix="tb_pick_")
    try:
        # ① 旧区间文件: 文件名里的 20260923 是下载时间戳, 内容只有 09-17~09-22
        old = mk(os.path.join(D, "门店下载_20260917至20260922_全部门店_1111111111_20260923164541.xlsx"),
                 ALL_H, [("2026-09-17", "S1", "店A", 1, 100),
                         ("2026-09-22", "S2", "店B", 2, 200)])
        # ② 本次真正要的 09-23(两份: 全部/爆品团)
        a23 = mk(os.path.join(D, "门店下载_20260923至20260923_全部门店_1111111111_20260924104434.xlsx"),
                 ALL_H, [("2026-09-23", "S1", "店A", 3, 300),
                         ("2026-09-23", "S2", "店B", 4, 400)])
        b23 = mk(os.path.join(D, "门店下载_20260923至20260923_全部门店_1111111111_20260924104440.xlsx"),
                 BOM_H, [("2026-09-23", "S1", "店A", 5, 500)])
        # ③ 0 字节残渣
        open(os.path.join(D, "门店下载_20260923至20260923_全部门店_1111111111_20260924104440_1.xlsx"),
             "w").close()

        assert report_kind(a23) == "全部", report_kind(a23)
        assert report_kind(b23) == "爆品团", report_kind(b23)
        assert report_kind(old) == "全部"
        print("✅ 表头签名识别类型: 全部/爆品团")

        all_f, bom_f = find_report_files(D, "2026-09-23", "2026-09-23")
        assert all_f == a23, f"「全部」选错了: {os.path.basename(all_f or '')}"
        assert bom_f == b23, f"「爆品团」选错了: {os.path.basename(bom_f or '')}"
        assert "20260923164541" not in (all_f or ""), "❌ 又选中了旧区间文件(下载时间戳被当日期)"
        assert "2026-09-23" in load_report_rows(all_f), "选中的文件内容必须含 09-23"
        print("✅ 回归: 名字含 20260923(下载时间戳)但内容是 09-17~09-22 的旧文件被正确排除")

        cands = report_file_candidates(D, "2026-09-23", "2026-09-23")
        stale = [c for c in cands if c["path"] == old][0]
        assert stale["covers"] is False, stale
        assert dates_span_text(stale["dates"]) == "2026-09-17~2026-09-22(2天)", stale["dates"]
        print(f"✅ 候选明细可自证: 旧文件内容日期={dates_span_text(stale['dates'])}")

        # ④ 只有旧文件时 → 必须返回 None(绝不"随便挑一个"), 让上层报清楚
        D2 = tempfile.mkdtemp(prefix="tb_pick2_")
        shutil.copy(old, os.path.join(D2, os.path.basename(old)))
        assert find_report_files(D2, "2026-09-23", "2026-09-23") == (None, None)
        shutil.rmtree(D2, ignore_errors=True)
        print("✅ 安全约束: 目录里没有含该日期的文件 → 返回 None(不硬凑)")

        # ⑤ 规范名往返 + 规范名优先
        name = canonical_report_name("全部", "2026-09-23", "2026-09-23",
                                     os.path.basename(a23))
        assert name.startswith("淘宝_全部_20260923_20260923_"), name
        assert parse_canonical_name(name) == ("全部", "20260923", "20260923")
        assert parse_canonical_name(os.path.basename(a23)) is None
        print("✅ 规范名: 生成/解析往返一致, 平台原名识别为「非规范名」")

        D3 = tempfile.mkdtemp(prefix="tb_pick3_")
        mark = mk(os.path.join(D3, name), ALL_H,
                  [("2026-09-23", "S9", "店Z", 9, 900)])        # 规范名但门店数少
        mk(os.path.join(D3, os.path.basename(a23)), ALL_H,       # 平台原名但门店数多
           [("2026-09-23", "S1", "店A", 1, 1), ("2026-09-23", "S2", "店B", 2, 2)])
        mk(os.path.join(D3, os.path.basename(b23)), BOM_H, [("2026-09-23", "S1", "店A", 5, 5)])
        a3, _ = find_report_files(D3, "2026-09-23", "2026-09-23")
        assert a3 == mark, f"规范名应优先: {os.path.basename(a3 or '')}"
        shutil.rmtree(D3, ignore_errors=True)
        print("✅ 同类型多份: 我方规范名优先(不看门店数)")

        # ⑥ 下载落地改名: 按内容签名 → 规范名(认不出则保持原名)
        from merchant_report.platforms.taobao import TaobaoAdapter
        D4 = tempfile.mkdtemp(prefix="tb_pick4_")
        raw = mk(os.path.join(D4, "门店下载_20260923至20260923_全部门店_1111111111_20260923164541.xlsx"),
                 ALL_H, [("2026-09-23", "S1", "店A", 1, 100)])
        ad = TaobaoAdapter.__new__(TaobaoAdapter)
        ad.sink = type("S", (), {"log": lambda self, m, level="info": None})()
        ad._dl_range = ("2026-09-23", "2026-09-23")
        out = ad._rename_canonical(pathlib.Path(raw))
        assert out.name.startswith("淘宝_全部_20260923_20260923_"), out.name
        assert pathlib.Path(out).exists() and not pathlib.Path(raw).exists()
        junk = mk(os.path.join(D4, "门店下载_异形表.xlsx"), ["日期", "别的字段"], [("2026-09-23", 1)])
        assert ad._rename_canonical(pathlib.Path(junk)).name == "门店下载_异形表.xlsx"
        ad._dl_range = ("", "")          # 没记区间 → 不改名
        assert ad._rename_canonical(pathlib.Path(junk)).name == "门店下载_异形表.xlsx"
        shutil.rmtree(D4, ignore_errors=True)
        print("✅ 下载落地改名: 内容签名 → 淘宝_全部_20260923_20260923_原名; 认不出/无区间则保持原名")

        print("✅ 报表选文件(名称 vs 内容)回归测试全部通过")
    finally:
        shutil.rmtree(D, ignore_errors=True)


if __name__ == "__main__":
    main()
