# -*- coding: utf-8 -*-
"""新建门店 Sheet 不能把样板表当天的数据一起复刻(2026-09-30 真机事故)。

现场: 京东那次任务里, 模板没有的门店(坡子街/天马公寓/杜甫江阁/浏城桥)被自动建表,
建表 = `copy_worksheet(样板表)`; 而样板表(第 3 个起的真门店表 = 铁道)在 40 分钟前的
管家任务里**已经被写过当天数据** → 4 张新表一开出来就带着「铁道」当天的
营业额 418.8 / 收入 347.8 / 单量 18 …(用户看到的是"这不是我这家店的数")。

规则: 复刻**结构**(表头/公式/列宽/A 列日期脚手架), 清空**数据**。
"""
import pathlib
import sys
import tempfile

import openpyxl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report import fill                                                # noqa: E402

MAINS = ("营业指标达成", "数据反馈看板", "Sheet1", "Sheet2")


def _mk_template(path):
    """造一个和用户模板同构的小模板: 2 张主表 + 1 张门店表(样板), 门店表已写好当天数据。"""
    wb = openpyxl.Workbook()
    wb.active.title = "营业指标达成"
    wb.create_sheet("数据反馈看板")
    wb.create_sheet("Sheet1")
    ws = wb.create_sheet("铁道")
    ws["A5"] = "日期"
    ws["E5"] = "总营业额"
    ws["M5"] = "差异"
    for i, r in enumerate(range(6, 12)):
        ws.cell(r, 1).value = 46294 + i          # A 列日期脚手架
        ws.cell(r, 5).value = 2732.7 if i == 0 else None      # 真数据(样板表当天写的)
        ws.cell(r, 3).value = "中雨~晴"
        ws.cell(r, 13).value = f"=E{r}-F{r}"                  # 公式列
    wb.save(path)
    return path


def test_new_sheet_has_structure_but_no_data():
    with tempfile.TemporaryDirectory() as d:
        tpl = _mk_template(f"{d}/t.xlsx")
        wb = openpyxl.load_workbook(tpl)
        ws, created = fill._ensure_sheet_in_wb(wb, "坡子街", main_sheets=MAINS)
        assert created and ws.title == "坡子街"
        assert ws["A6"].value == 46294, "A 列日期脚手架必须保留(写入靠它定位行号)"
        assert ws["E6"].value is None, f"样例表数据被复刻进新表: E6={ws['E6'].value!r}"
        assert ws["C6"].value is None, f"天气被复刻进新表: C6={ws['C6'].value!r}"
        assert str(ws["M6"].value).startswith("="), "公式列必须保留(结构)"
        assert ws["E5"].value == "总营业额", "表头必须保留"
        print("✅ 新建 Sheet: 表头/公式/A 列日期保留, 样板表数据已清空")


def test_existing_sheet_untouched():
    with tempfile.TemporaryDirectory() as d:
        tpl = _mk_template(f"{d}/t.xlsx")
        wb = openpyxl.load_workbook(tpl)
        ws, created = fill._ensure_sheet_in_wb(wb, "铁道", main_sheets=MAINS)
        assert not created and ws["E6"].value == 2732.7, "已存在的门店表不能被清"
        print("✅ 已存在的门店表原样不动")


def test_fill_many_creates_clean_sheet_and_writes():
    """端到端: 新表 + 写入同一次完成 → 新表里只有本次写的数据, 没有样板表残留。"""
    with tempfile.TemporaryDirectory() as d:
        tpl = _mk_template(f"{d}/t.xlsx")
        jobs = [("铁道", "2026-09-29", {"E": 100.0}), ("浏城桥", "2026-09-29", {"E": 7.0})]
        res = fill.fill_many(tpl, jobs, mode="top", main_sheets=MAINS)
        got = {r[0]: r[2] for r in res}
        assert got["浏城桥"]["row"] == 6, got
        wb = openpyxl.load_workbook(tpl)
        ws = wb["浏城桥"]
        assert ws["E6"].value == 7.0, ws["E6"].value
        assert ws["C6"].value is None, f"样板表天气残留: {ws['C6'].value!r}"
        print("✅ fill_many 建表+写入: 新表只有本次数据(无样板残留)")


def test_ensure_sheet_on_disk_clears():
    with tempfile.TemporaryDirectory() as d:
        tpl = _mk_template(f"{d}/t.xlsx")
        assert fill.ensure_sheet(tpl, "杜甫江阁", main_sheets=MAINS) is True
        wb = openpyxl.load_workbook(tpl)
        assert wb["杜甫江阁"]["E6"].value is None
        assert wb["杜甫江阁"]["A6"].value == 46294
        print("✅ ensure_sheet(落盘版)同样清空数据区")


def test_run_log_path():
    from merchant_report.task_main import _run_log_path
    with tempfile.TemporaryDirectory() as d:
        p = _run_log_path(d, "meituan_gj")
        assert p.startswith(d + "/logs/run_") and p.endswith("_meituan_gj.ndjson"), p
        assert pathlib.Path(d, "logs").is_dir()
        print(f"✅ 运行日志落盘路径: …/{pathlib.Path(p).name}")


def main():
    for fn in (test_new_sheet_has_structure_but_no_data, test_existing_sheet_untouched,
               test_fill_many_creates_clean_sheet_and_writes, test_ensure_sheet_on_disk_clears,
               test_run_log_path):
        fn()
    print("✅ 新建门店 Sheet(结构复刻/数据清空)+运行日志落盘 单测全部通过")


if __name__ == "__main__":
    main()
