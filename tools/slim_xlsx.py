#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「整行套格式」撑大的模板 xlsx 瘦身 —— 独立工具(不走程序填表流程也能用)。

背景: 《运营报表模板10.13》被 WPS 做过整行/整表套格式 → 27 张表里 23 张的 sheet XML 各约
8.95MB(解压合计 183.7MB), 单表 33 万个单元格里只有约 3 千个有内容, 其余都是"无值但带样式"
的空格, 一路铺到 XFD(16384) 列。程序载入→保存会原样带出 → 输出 17.8~21.6MB。
本工具按内核同一套口径裁剪(见 kernel/merchant_report/xlsx_slim.py), 实测 21.6MB → 1.2MB。

用法:
    python3 tools/slim_xlsx.py 模板.xlsx                    # → 同目录 模板-slim.xlsx
    python3 tools/slim_xlsx.py 模板.xlsx /tmp/out.xlsx      # 指定输出
    python3 tools/slim_xlsx.py 模板.xlsx --json             # 机器可读摘要

安全约束:
  · **绝不原地覆盖**: 输出路径与输入相同直接拒绝(瘦身是改写几百万格的事故面, 必须留原件);
  · 文件已存在 → 拒绝, 不覆盖(--force 才允许);
  · 内容格逐格保留(只删 value is None 的越界格), 合并区/行高/列宽/打印区一律不动。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "kernel"))     # 用仓库内核同一份口径, 不另起炉灶

import openpyxl                                                        # noqa: E402
from merchant_report.xlsx_slim import (SLIM_COL_MARGIN, SLIM_ROW_MARGIN,   # noqa: E402
                                       slim_workbook)


def _mb(path):
    return os.path.getsize(path) / 1024.0 / 1024.0


def _sheet_xml_sizes(path):
    """{sheet 名: 未压缩 XML 字节数} —— 给"主要表"排序用。

    属性顺序在 xlsx 里没有保证(WPS 写的 r:id 在后面, openpyxl 写的 Target 在前),
    所以逐个元素抓属性, 不靠单条正则的顺序假设。
    """
    import re
    import zipfile

    def attr(el, name):
        m = re.search(rf'\b{name}="([^"]*)"', el)
        return m.group(1) if m else None

    out = {}
    try:
        with zipfile.ZipFile(path) as z:
            wb_xml = z.read("xl/workbook.xml").decode("utf-8", "ignore")
            rels_xml = z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "ignore")
            rid2part = {}
            for el in re.findall(r"<Relationship\b[^>]*/>", rels_xml):
                rid, tgt = attr(el, "Id"), attr(el, "Target")
                if not rid or not tgt:
                    continue
                part = tgt.lstrip("/")
                if not part.startswith("xl/"):
                    part = "xl/" + part.lstrip("./")
                rid2part[rid] = part
            for el in re.findall(r"<sheet\b[^>]*/>", wb_xml):
                try:
                    title, rid = attr(el, "name"), attr(el, "r:id")
                except TypeError:
                    continue
                part = rid2part.get(rid)
                if not title or not part:
                    continue
                try:
                    out[title] = z.getinfo(part).file_size
                except KeyError:
                    continue
    except Exception:
        return {}
    return out


def _defused_name(path):
    base, ext = os.path.splitext(path)
    return f"{base}-slim{ext or '.xlsx'}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="模板 xlsx 瘦身(剪掉无值但带样式的越界空格)")
    ap.add_argument("input", help="输入 xlsx")
    ap.add_argument("output", nargs="?", default=None, help="输出 xlsx(缺省: 同目录 <原名>-slim.xlsx)")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON 摘要")
    ap.add_argument("--force", action="store_true", help="输出文件已存在时覆盖")
    ap.add_argument("--col-margin", type=int, default=SLIM_COL_MARGIN)
    ap.add_argument("--row-margin", type=int, default=SLIM_ROW_MARGIN)
    args = ap.parse_args(argv)

    src = os.path.abspath(args.input)
    dst = os.path.abspath(args.output) if args.output else _defused_name(src)
    if not os.path.exists(src):
        print(f"❌ 输入不存在: {src}", file=sys.stderr)
        return 2
    if os.path.exists(dst) and os.path.samefile(src, dst):
        print("❌ 拒绝原地覆盖(输入=输出): 瘦身必须保留原件, 请换输出路径", file=sys.stderr)
        return 2
    if os.path.exists(dst) and not args.force:
        print(f"❌ 输出已存在: {dst}(加 --force 覆盖)", file=sys.stderr)
        return 2

    before_xml = _sheet_xml_sizes(src)

    t0 = time.time()
    wb = openpyxl.load_workbook(src)
    load_s = time.time() - t0
    t0 = time.time()
    st = slim_workbook(wb, col_margin=args.col_margin, row_margin=args.row_margin)
    slim_s = time.time() - t0
    t0 = time.time()
    wb.save(dst)
    wb.close()
    save_s = time.time() - t0
    after_xml = _sheet_xml_sizes(dst)

    before_mb, after_mb = _mb(src), _mb(dst)
    rows = []
    for title, n in before_xml.items():
        rows.append((title, n, after_xml.get(title, 0)))
    rows.sort(key=lambda r: r[1], reverse=True)

    summary = {
        "input": src, "output": dst,
        "bytes_before": os.path.getsize(src), "bytes_after": os.path.getsize(dst),
        "mb_before": round(before_mb, 2), "mb_after": round(after_mb, 2),
        "removed": st["removed"], "kept_blanks": st["kept_blanks"],
        "col_bound": st["col_bound"], "row_bound": st["row_bound"],
        "skipped_sheets": st["skipped_sheets"], "sheets_touched": st["sheets_touched"],
        "col_margin": args.col_margin, "row_margin": args.row_margin,
        "seconds": {"load": round(load_s, 1), "slim": round(slim_s, 1), "save": round(save_s, 1)},
        "top_sheets": [{"sheet": t, "xml_before": b, "xml_after": a} for t, b, a in rows[:8]],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0

    print(f"输入: {src}  ({before_mb:.2f} MB)")
    print(f"输出: {dst}  ({after_mb:.2f} MB)   "
          f"↓ {(1 - after_mb / before_mb) * 100:.1f}%" if before_mb else "")
    print(f"剪掉空格式格: {st['removed']} 个 / 涉及 {st['sheets_touched']} 张表"
          f"(边界 列≤{st['col_bound']} 行≤{st['row_bound']}, 边距 +{args.col_margin}/+{args.row_margin}, "
          f"保留空格 {st['kept_blanks']})")
    if st["skipped_sheets"]:
        print(f"⚠️ 打印区解析失败跳过(未剪): {', '.join(st['skipped_sheets'])}")
    print(f"耗时: 读 {load_s:.1f}s / 剪 {slim_s:.1f}s / 写 {save_s:.1f}s")
    print("主要表 sheet XML(未压缩字节):")
    for title, b, a in rows[:8]:
        pct = (1 - a / b) * 100 if b else 0
        print(f"  {title:24s} {b / 1048576:8.2f} MB → {a / 1048576:8.2f} MB  (↓{pct:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
