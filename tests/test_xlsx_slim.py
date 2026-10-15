# -*- coding: utf-8 -*-
"""M5 单测: 模板 xlsx 瘦身(剪掉 WPS「整行套格式」留下的无值空格)。

症状(2026-10-15 定位): 模板 27 张表里 23 张 sheet XML 各约 8.95MB(解压 183.7MB), 单表
33 万个 <c> 里只有约 3 千个有内容 —— 其余是"无值但带样式"的空格, 从数据区铺到 XFD 列。
程序 openpyxl 载入→保存原样带出 → 输出 17.8~21.6MB。实测裁剪后 21.6MB → 1.2MB。

本测试全用**合成小夹具**(不碰真实 21.6MB 模板; 无 sleep, 全部瞬时):
  ① 越界无值格被剪净, 内容格逐一保留(值/公式一字不差)
  ② 合并区延伸超出内容边界时不被剪(那是用户真会看到的区域)
  ③ 只删 value is None —— 0 / False / 空字符串都算有值, 一个都不许丢
  ④ 行高/列宽/打印区保留; 幂等(二次 removed=0); 存盘后 openpyxl 可正常载入
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))

import openpyxl                                                       # noqa: E402
from merchant_report import xlsx_slim as S                            # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def count_blank_outside(ws, col_bound, row_bound):
    """越界且无值的格子数(用来断言"剪干净了")。"""
    n = 0
    for (r, c), cell in ws._cells.items():
        if (c > col_bound or r > row_bound) and cell.value is None:
            n += 1
    return n


def mk_inflated(path, col_bound_hint=None):
    """合成「整行膨胀」模板: 内容只占 A1:J20, 但 K 列之后铺满带样式的空格(仿 WPS 整行套格式)。

    · 数据区 A1:J20(含数字/文本/公式)
    · 样式空格: 第 1~60 行 × 第 11~400 列(**无值**但带 fill/边框)
    · C 列有显式列宽、第 3 行有显式行高(必须活过瘦身)
    """
    from openpyxl.styles import PatternFill, Border, Side
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "门店A"
    fill = PatternFill("solid", fgColor="FFF2CC")
    thin = Side(style="thin")
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    for r in range(1, 21):
        for c in range(1, 11):
            ws.cell(row=r, column=c, value=(r * 10 + c))
    ws["A1"] = "日期"
    ws["B2"] = 0.0                     # 有值的 0
    ws["C3"] = 46266
    ws["E6"] = "=SUM(E1:E5)"           # 公式也必须有值
    for r in range(1, 61):
        for c in range(11, 401):
            cell = ws.cell(row=r, column=c)
            cell.fill = fill
            cell.border = bd
    ws.column_dimensions["C"].width = 24.0
    ws.row_dimensions[3].height = 33.0
    wb.save(path)
    return path


def test_clip_and_keep():
    print("=== A. 越界无值格剪净 + 内容格逐一保留 ===")
    with tempfile.TemporaryDirectory() as d:
        tpl = mk_inflated(os.path.join(d, "t.xlsx"))
        wb = openpyxl.load_workbook(tpl)
        before = {k: v.value for k, v in wb["门店A"]._cells.items() if v.value is not None}
        st = S.slim_workbook(wb)
        ws = wb["门店A"]
        check("有剪到东西", st["removed"] > 0, str(st))
        check("越界无值格 = 0", count_blank_outside(ws, st["col_bound"], st["row_bound"]) == 0,
              f"还剩 {count_blank_outside(ws, st['col_bound'], st['row_bound'])}")
        after = {k: v.value for k, v in ws._cells.items() if v.value is not None}
        check("内容格逐一保留(值零差异)", before == after,
              f"丢 {set(before) - set(after)} 多 {set(after) - set(before)}")
        check("公式格保留", ws["E6"].value == "=SUM(E1:E5)", repr(ws["E6"].value))
        check("行高保留", ws.row_dimensions[3].height == 33.0, str(ws.row_dimensions[3].height))
        check("列宽保留", ws.column_dimensions["C"].width == 24.0, str(ws.column_dimensions["C"].width))
        check("边界 = 内容(10 列/20 行) + 边距(30/15)",
              (st["col_bound"], st["row_bound"]) == (40, 35), str((st["col_bound"], st["row_bound"])))
        check("边界内空格保留(不误剪)", st["kept_blanks"] > 0, str(st))
        # 存盘后能正常载入 + 不再反弹
        out = os.path.join(d, "o.xlsx")
        wb.save(out)
        wb.close()
        rb = openpyxl.load_workbook(out)
        rws = rb["门店A"]
        check("存盘后可载入且越界无值格仍为 0",
              count_blank_outside(rws, st["col_bound"], st["row_bound"]) == 0)
        check("存盘后内容仍在", rws["C3"].value == 46266, repr(rws["C3"].value))
        rb.close()
        # 幂等: 再剪一次 removed == 0
        st2 = S.slim_workbook(openpyxl.load_workbook(out))
        check("幂等(二次 removed=0)", st2["removed"] == 0, str(st2))


def test_merge_beyond_content():
    print("=== B. 合并区延伸超出内容边界 → 不剪(用户在意的可见区域) ===")
    with tempfile.TemporaryDirectory() as d:
        tpl = os.path.join(d, "m.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "门店A"
        for r in range(1, 11):
            for c in range(1, 6):
                ws.cell(row=r, column=c, value=r * c)
        ws.merge_cells("MJ1:MJ50")        # MJ=348 列 / 50 行: 远超内容(5 列/10 行)
        from openpyxl.styles import PatternFill
        fill = PatternFill("solid", fgColor="DDDDDD")
        for r in range(1, 61):
            for c in range(6, 401):
                ws.cell(row=r, column=c).fill = fill
        st = S.slim_workbook(wb)
        check("边界含合并区延伸(348+30 / 50+15)",
              (st["col_bound"], st["row_bound"]) == (378, 65), str((st["col_bound"], st["row_bound"])))
        check("合并区内空格没被剪", ws.cell(row=50, column=348).value is None
              and (50, 348) in ws._cells, "合并区 348 列被剪了")
        check("越界无值格 = 0", count_blank_outside(ws, st["col_bound"], st["row_bound"]) == 0)
        check("合并区保留", [str(r) for r in ws.merged_cells.ranges] == ["A1:A1", "MJ1:MJ50"]
              or "MJ1:MJ50" in [str(r) for r in ws.merged_cells.ranges],
              str([str(r) for r in ws.merged_cells.ranges]))
        check("内容格仍在", ws["E10"].value == 50, repr(ws["E10"].value))


def test_only_none_is_deleted():
    print("=== C. 只删 value is None: 0 / False / '' 都算有值 ===")
    with tempfile.TemporaryDirectory() as d:
        tpl = os.path.join(d, "v.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "门店A"
        for r in range(1, 11):
            for c in range(1, 6):
                ws.cell(row=r, column=c, value=1)
        # 远列(400)上的"假值": 它们是内容 → 参与边界计算, 绝不能被当空格剪掉
        ws.cell(row=1, column=400, value=0)
        ws.cell(row=2, column=400, value=False)
        ws.cell(row=3, column=400, value="")
        # 边界(=430)之外的纯空格 → 应当被剪掉(证明这条不是"整表都没剪")
        for r in range(1, 31):
            for c in range(431, 471):
                ws.cell(row=r, column=c).number_format = "0.00"
        for r in range(1, 61):
            for c in range(6, 100):
                ws.cell(row=r, column=c).number_format = "0.00"
        st = S.slim_workbook(wb)
        check("边界被 0/False/'' 撑开(400+30)", st["col_bound"] == 430, str(st["col_bound"]))
        check("0 保留", ws.cell(row=1, column=400).value == 0, repr(ws.cell(row=1, column=400).value))
        check("False 保留", ws.cell(row=2, column=400).value is False,
              repr(ws.cell(row=2, column=400).value))
        check("'' 保留(且仍是空串)", ws.cell(row=3, column=400).value == "",
              repr(ws.cell(row=3, column=400).value))
        check("边界外的样式空格被剪", (5, 450) not in ws._cells, "第 5 行 450 列没剪掉")
        check("边界内的样式空格保留", (5, 50) in ws._cells and ws.cell(row=5, column=50).value is None)
        check("有删有留", st["removed"] > 0, str(st))
        # 存盘往返后三类值不变。
        # ⚠️ '' 特殊: openpyxl 把它写成 <c t="inlineStr"/> 空内联串, 读回来是 None(库自身行为,
        # 与本模块无关 —— 我们既没删它也没改它)。所以这格断言"格子还在", 不是"值还是 ''"。
        out = os.path.join(d, "v2.xlsx")
        wb.save(out)
        rb = openpyxl.load_workbook(out)
        rws = rb["门店A"]
        check("往返后 0/False 不变",
              (rws.cell(row=1, column=400).value, rws.cell(row=2, column=400).value) == (0, False),
              repr((rws.cell(row=1, column=400).value, rws.cell(row=2, column=400).value)))
        check("往返后 '' 格仍存在(openpyxl 读回为 None, 库行为)", (3, 400) in rws._cells,
              str(sorted(rws._cells)[:5]))
        rb.close()


def test_print_area_extends_bound():
    print("=== D. 打印区延伸并入边界; 垃圾 definedName 不碰 ===")
    with tempfile.TemporaryDirectory() as d:
        tpl = os.path.join(d, "p.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "门店A"
        for r in range(1, 6):
            for c in range(1, 4):
                ws.cell(row=r, column=c, value=r + c)
        ws2 = wb.create_sheet("门店B")
        ws2["A1"] = 1
        ws2.print_area = "A1:Z120"          # 打印区远大于内容 → 边界必须跟着外扩
        from openpyxl.workbook.defined_name import DefinedName
        wb.defined_names.add(DefinedName("a" * 64, attr_text="#REF!"))   # 本模板里的真实垃圾
        st = S.slim_workbook(wb)
        check("打印区并入边界(26+30 / 120+15)", (st["col_bound"], st["row_bound"]) == (56, 135),
              str((st["col_bound"], st["row_bound"])))
        check("垃圾 definedName 仍在(不许删)",
              "a" * 64 in wb.defined_names, str(list(wb.defined_names.keys())))
        check("打印区仍在", "$Z$120" in str(ws2.print_area), repr(ws2.print_area))
        check("未跳过任何表", st["skipped_sheets"] == [], str(st["skipped_sheets"]))


def test_switch_resolution():
    print("=== E. 开关解析: 环境变量 > cfg > 默认 True ===")
    old = os.environ.pop("MRC_SLIM_XLSX", None)
    try:
        check("默认 True", S.slim_enabled(cfg={}) is True)
        check("cfg 显式关", S.slim_enabled(cfg={"defaults": {"slim_xlsx": False}}) is False)
        check("cfg 显式开", S.slim_enabled(cfg={"defaults": {"slim_xlsx": True}}) is True)
        for raw, want in (("0", False), ("false", False), ("Off", False), ("1", True), ("yes", True)):
            check(f"env MRC_SLIM_XLSX={raw} → {want}", S.slim_enabled(cfg={}, env=raw) is want)
        check("env 压过 cfg(救急用)",
              S.slim_enabled(cfg={"defaults": {"slim_xlsx": True}}, env="0") is False)
    finally:
        if old is not None:
            os.environ["MRC_SLIM_XLSX"] = old
    check("常量边距", (S.SLIM_COL_MARGIN, S.SLIM_ROW_MARGIN) == (30, 15),
          str((S.SLIM_COL_MARGIN, S.SLIM_ROW_MARGIN)))


def test_multi_sheet_global_bound():
    print("=== F. 边界跨全部表取全局最大(表间同口径) ===")
    with tempfile.TemporaryDirectory() as d:
        tpl = os.path.join(d, "g.xlsx")
        wb = openpyxl.Workbook()
        a = wb.active
        a.title = "窄表"
        a["A1"] = 1
        b = wb.create_sheet("宽表")
        b.cell(row=30, column=100, value=7)
        from openpyxl.styles import PatternFill
        f = PatternFill("solid", fgColor="EEEEEE")
        for r in range(1, 41):
            for c in range(1, 301):
                a.cell(row=r, column=c).fill = f
        st = S.slim_workbook(wb)
        check("全局边界由最宽表(100 列/30 行)决定",
              (st["col_bound"], st["row_bound"]) == (130, 45), str((st["col_bound"], st["row_bound"])))
        check("窄表在全局边界内的空格未被剪",
              (1, 100) in a._cells and a.cell(row=1, column=100).value is None)
        check("两表越界无值格 = 0",
              count_blank_outside(a, st["col_bound"], st["row_bound"]) == 0
              and count_blank_outside(b, st["col_bound"], st["row_bound"]) == 0)


def test_print_area_parse_failure_skips_sheet():
    print("=== G. 打印区解析失败 → 跳过该表 + 记账(绝不拿猜的边界去剪) ===")
    with tempfile.TemporaryDirectory() as d:
        tpl = os.path.join(d, "q.xlsx")
        wb = openpyxl.Workbook()
        a = wb.active
        a.title = "好表"
        a["A1"] = 1
        b = wb.create_sheet("坏表")
        b["A1"] = 2
        from openpyxl.styles import PatternFill
        fill = PatternFill("solid", fgColor="CCCCCC")
        for r in range(1, 41):
            for c in range(3, 201):
                b.cell(row=r, column=c).fill = fill

        class _BoomPrintArea:
            @property
            def ranges(self):
                raise ValueError("打印区是 #REF! 之类解析不了的值")

        b._print_area = _BoomPrintArea()
        st = S.slim_workbook(wb)
        check("坏表进 skipped_sheets", st["skipped_sheets"] == ["坏表"], str(st["skipped_sheets"]))
        check("坏表一格未动(200 列的样式空格仍在)",
              b.cell(row=40, column=200).value is None and (40, 200) in b._cells)
        check("好表照常处理", st["col_bound"] > 0, str(st))
        check("坏表的越界空格没被剪(仍可数出来)",
              count_blank_outside(b, 1, 1) > 0, str(count_blank_outside(b, 1, 1)))


def test_bounds_from_area_text():
    print("=== H. 打印区文本解析(多段/垃圾值) ===")
    check("多段取最大列/行", S._bounds_from_area_text("'A'!$A$1:$H$40,'A'!$D$1:$E$5") == (8, 40),
          str(S._bounds_from_area_text("'A'!$A$1:$H$40,'A'!$D$1:$E$5")))
    check("空串 → (0, 0)", S._bounds_from_area_text("") == (0, 0))
    try:
        S._bounds_from_area_text("#REF!")
        check("垃圾值 → 抛错(交调用方跳过该表)", False)
    except ValueError:
        check("垃圾值 → 抛错(交调用方跳过该表)", True)


def main():
    print("M5 单测: xlsx 瘦身(slim_workbook)")
    check("模块 API", hasattr(S, "slim_workbook") and hasattr(S, "slim_enabled"))
    test_clip_and_keep()
    test_merge_beyond_content()
    test_only_none_is_deleted()
    test_print_area_extends_bound()
    test_switch_resolution()
    test_multi_sheet_global_bound()
    test_print_area_parse_failure_skips_sheet()
    test_bounds_from_area_text()
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
