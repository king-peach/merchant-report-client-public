#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理淘宝「商圈TOP」列里的历史错值(必须跑, 否则错数据一直留在模板里)。

背景(2026-09-30 真机核实):
    品牌版淘宝流量页**没有**「商圈同行/前10%」对比, 漏斗第二列其实是「顾客维度 = 新客」。
    它 = 64,454 = 报表 Σ新客曝光(三项全等) → 旧代码把这串新客数当「商圈TOP」写进了模板。
    现在代码已改成 TOP 列不写(fill 对 None 是"跳过", 所以历史错值不会被自动覆盖掉)。

本脚本只清「已知错误」的单元格, 不动其它数据:
  · 淘宝TOP静态列: DO/DR/DU/DX/EA、ED/EG/EJ/EM/EP/ES、FA/FD/FG/FJ/FM/FP/FS/FV/FY/GB/GE
  · 淘宝整体口径被写进新客/老客列的三处: EC/EF/EI、EL/EO/ER(旧 bug: 三态同值)
    —— 清零后由下次运行按报表逐店口径重写(不写比写错好)。
用法:
    python3 tools/clean_taobao_top_columns.py            # 演练(只打印, 不改)
    python3 tools/clean_taobao_top_columns.py --apply    # 备份后真正清理
"""
import argparse
import os
import shutil
import sys
import time

import openpyxl

XL = os.path.expanduser("~/Downloads/商家报表整理/运营报表模板.xlsx")
TOP_COLS = ["DO", "DR", "DU", "DX", "EA", "ED", "EG", "EJ", "EM", "EP", "ES",
            "FA", "FD", "FG", "FJ", "FM", "FP", "FS", "FV", "FY", "GB", "GE"]
SEG_COLS = ["EC", "EF", "EI", "EL", "EO", "ER"]   # 淘宝主站新客/老客(旧代码写的是整体值)
HEADER_ROWS = (1, 2, 3, 4, 5)                     # 表头区不动


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入(默认只演练)")
    args = ap.parse_args()

    wb = openpyxl.load_workbook(XL)
    hits = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=max(HEADER_ROWS) + 1):
            for c in row:
                col = "".join(ch for ch in c.coordinate if ch.isalpha())
                v = c.value
                # ⚠️ 只清**静态值**: 这些列在别的表里可能是模板公式(=DI6/DH6 之类, 见天麓),
                # 公式绝不能删。演练时会打印全部命中, 先看清楚再 --apply。
                if col in TOP_COLS + SEG_COLS and v not in (None, "") \
                        and not (isinstance(v, str) and v.startswith("=")):
                    hits.append((ws.title, c.coordinate, v))

    if not hits:
        print("没有需要清理的单元格 ✓")
        return 0
    print(f"发现 {len(hits)} 个待清理单元格(淘宝TOP列 + 旧bug污染的淘宝新客/老客列):")
    for t, coord, v in hits[:40]:
        print(f"   {t:>10} {coord:>6} = {v!r}")
    if len(hits) > 40:
        print(f"   … 另 {len(hits) - 40} 个")
    if not args.apply:
        print("\n(演练模式, 未修改; 加 --apply 真清理)")
        return 0

    bak = XL.replace(".xlsx", f"_备份_清理淘宝TOP_{time.strftime('%Y%m%d-%H%M%S')}.xlsx")
    shutil.copy2(XL, bak)
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=max(HEADER_ROWS) + 1):
            for c in row:
                col = "".join(ch for ch in c.coordinate if ch.isalpha())
                v = c.value
                if col in TOP_COLS + SEG_COLS and v not in (None, "") \
                        and not (isinstance(v, str) and v.startswith("=")):
                    c.value = None
    wb.save(XL)
    print(f"\n✅ 已清理 {len(hits)} 个单元格; 备份: {bak}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
