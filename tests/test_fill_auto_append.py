# -*- coding: utf-8 -*-
"""写入位置新口径单测(2026-09-28 用户变更):

① 不再"插到第一行": 先在模板里找**选定的日期**那一行 → 有就填那一行; 没有就**追加到最后**。
② 追加行要像普通数据行: 克隆上一行样式 + **相对平移复制公式**(模板差异列不能空着)。
③ 新建门店 Sheet 的样板表 = **第 3 个 sheet**(不是 Sheet2) —— 模板前两个 sheet 不一定是
   门店统计表(可能是封面/汇总)。
"""
import pathlib
import shutil
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
import openpyxl                                                             # noqa: E402
from merchant_report import config as C                                     # noqa: E402
from merchant_report.fill import (_ensure_sheet_in_wb, ensure_sheet,          # noqa: E402
                                  pick_base_sheet, resolve_row)


def mk_template(path, sheets=("Sheet2",), dates=(), marker=None):
    wb = openpyxl.Workbook()
    wb.active.title = sheets[0]
    for s in sheets[1:]:
        wb.create_sheet(s)
    if marker:
        for s, val in marker.items():
            wb[s]["Z1"] = val              # 每个 sheet 放个标记, 用来验证"复制了哪一个"
    ws = wb[sheets[0]]
    ws["A5"], ws["B5"], ws["C5"], ws["D5"] = "日期", "星期", "数据", "差异"
    r = 6
    for d in dates:
        ws[f"A{r}"] = datetime.strptime(d, "%Y-%m-%d")
        ws[f"A{r}"].number_format = 'm"月"d"日"'
        ws[f"C{r}"] = 100 + r
        ws[f"D{r}"] = f"=C{r}-1"           # 公式列(差异)
        r += 1
    wb.save(path)
    return path


def main():
    D = tempfile.mkdtemp(prefix="fill_auto_")

    # ① 模板里**已有**该日期的行 → 填那一行(不新增、不插到第一行)
    p1 = mk_template(D + "/a.xlsx", dates=["2026-09-27", "2026-09-28", "2026-09-29"])
    wb = openpyxl.load_workbook(p1)
    ws = wb["Sheet2"]
    row, why = resolve_row(ws, "2026-09-29", mode="auto")
    assert (row, why) == (8, "existing"), (row, why)
    print(f"✅ 模板已预设 2026-09-29 → 命中第 {row} 行({why}), 不插第一行也不追加")

    # ② 模板里**没有**该日期 → 追加到最后一行之后, 且公式列被复制(相对平移)
    p2 = mk_template(D + "/b.xlsx", dates=["2026-09-27", "2026-09-28"])
    wb = openpyxl.load_workbook(p2)
    ws = wb["Sheet2"]
    row, why = resolve_row(ws, "2026-09-30", mode="auto")
    assert (row, why) == (8, "appended(after 7)"), (row, why)
    assert str(ws["A8"].value)[:10] == "2026-09-30", ws["A8"].value
    assert ws["B8"].value, "应填星期"
    assert ws["D8"].value == "=C8-1", f"公式列必须相对平移复制, 实际 {ws['D8'].value!r}"
    assert ws["C8"].value is None, "上一行的静态值不能串到追加行"
    print(f"✅ 模板无该日期 → 追加到第 {row} 行({why}), 公式列 ={ws['D8'].value}")

    # ③ 默认配置: fill_mode 必须是 auto(用户口径)
    assert C._defaults()["defaults"]["fill_mode"] == "auto", C._defaults()["defaults"]
    print("✅ 默认 fill_mode = auto(不再插第一行)")

    # ④ 新建门店 Sheet 的样板表 = 第 3 个 sheet(不是 Sheet2)
    p3 = D + "/c.xlsx"
    mk_template(p3, sheets=("Sheet2", "Sheet3", "岚霞路", "高桥"),
                dates=["2026-09-27"], marker={"Sheet2": "S2", "Sheet3": "S3",
                                              "岚霞路": "LAN", "高桥": "GAO"})
    wb = openpyxl.load_workbook(p3)
    # 模板第 3 个位置已是"主数据表"(岚霞路) → 样板必须**跳过它**, 取后面第一张真门店表(高桥)
    assert pick_base_sheet(wb) == "高桥", pick_base_sheet(wb)
    ws2, created = _ensure_sheet_in_wb(wb, "新店")
    assert created and ws2["Z1"].value == "GAO", f"应跳过主表复制门店表, 实际 {ws2['Z1'].value!r}"
    print("✅ 新建 Sheet 跳过主数据表, 复刻后面第一张门店表(高桥)的结构")

    # ⑤ 调用方明确指定 base_sheet 时优先用它
    wb = openpyxl.load_workbook(p3)
    ws3, _ = _ensure_sheet_in_wb(wb, "新店2", base_sheet="岚霞路")
    assert ws3["Z1"].value == "LAN", ws3["Z1"].value
    print("✅ 显式 base_sheet 优先(岚霞路, 即便它是主表)")

    # ⑥ ensure_sheet(落盘版)同样按第 3 个 sheet 复制
    ok = ensure_sheet(p3, "新店3")
    assert ok, "应新建成功"
    wb = openpyxl.load_workbook(p3)
    assert wb["新店3"]["Z1"].value == "GAO", wb["新店3"]["Z1"].value
    print("✅ ensure_sheet(落盘)同样跳过主表, 复刻门店表结构")

    # ⑦ 只有 1~2 个 sheet 的模板 → 退回旧行为(向后兼容)
    p4 = mk_template(D + "/d.xlsx", sheets=("Sheet2", "Sheet3"), dates=["2026-09-27"])
    wb = openpyxl.load_workbook(p4)
    assert pick_base_sheet(wb) == "Sheet2", pick_base_sheet(wb)
    print("✅ 老模板(仅 Sheet2/Sheet3): 样板仍取 Sheet2, 行为不变")

    # ⑧ 主数据表(汇总)默认 = 第 3 个 sheet; 撞车保护: 若它同时是本次要写的门店表 → 回退 Sheet2
    from merchant_report.fill import default_sheet_name, main_sheet_in, resolve_main
    p5 = D + "/e.xlsx"
    mk_template(p5, sheets=("Sheet2", "Sheet3", "岚霞路", "高桥"), dates=["2026-09-27"])
    assert default_sheet_name(p5) == "岚霞路", default_sheet_name(p5)
    wb = openpyxl.load_workbook(p5)
    assert main_sheet_in(wb) == "岚霞路", main_sheet_in(wb)
    print("✅ 主数据表默认 = 第 3 个 sheet(岚霞路)")
    assert resolve_main(p5, None, []) == ("岚霞路", None), resolve_main(p5, None, [])
    got = resolve_main(p5, None, ["岚霞路"])
    assert got == ("Sheet2", "岚霞路"), got
    print(f"✅ 撞车保护: 第 3 个 sheet 又是门店表 → 汇总回退 {got[0]}(避免冲掉门店数据)")
    # 配置可显式钉名(跨轮稳定, 不受"新建 sheet 改变索引"影响)
    assert resolve_main(p5, {"defaults": {"main_sheet": "高桥"}}, []) == ("高桥", None)
    print("✅ config defaults.main_sheet 可显式钉名")
    # 第 3 个是「门店名映射表」(用户新模板就是这种) → 汇总绝不能写进去
    p7 = D + "/g.xlsx"
    wb0 = openpyxl.Workbook(); wb0.active.title = "Sheet2"
    wb0.create_sheet("Sheet3")
    mp = wb0.create_sheet("Sheet1")
    mp["B2"], mp["E2"] = "门店", "美团门店名称"      # 映射表特征
    mp["B3"], mp["E3"] = "高桥", "示例柠檬茶（长沙高桥店）"
    wb0.create_sheet("高桥"); wb0.save(p7)
    got = resolve_main(p7, None, ["高桥"])
    assert got == ("Sheet2", "Sheet1"), got
    print(f"✅ 第 3 个是门店名映射表 → 汇总回退 {got[0]}(不污染映射数据)")

    # 只有 2 个 sheet → 退回历史值 Sheet2
    p6 = D + "/f.xlsx"
    mk_template(p6, sheets=("Sheet2", "Sheet3"), dates=["2026-09-27"])
    assert default_sheet_name(p6) == "Sheet2", default_sheet_name(p6)
    print("✅ 模板不足 3 个 sheet: 主表 = Sheet2(老模板行为不变)")

    shutil.rmtree(D, ignore_errors=True)
    print("✅ 写入位置/样板表新口径测试全部通过")


if __name__ == "__main__":
    main()
