# -*- coding: utf-8 -*-
"""对账页(推广费流水)日期交互: 探测 → 键入 → 读回 → 日历兜底 → 重试2次。

真机背景(2026-10-14, 17/17 全失败):
    日志 `⚠️ 对账页日期设置未生效 → CE/CJ 留空`(逐店逐日), step 结果 `流水 0 店 / 概览 0 店`.
    读回证据 = 两个日期框都是空串 `['', '']` —— 键盘输入在这页不生效
    (与淘宝下载页「React 受控输入不认程序写入」同类: 直接写入未生效 → 改日历点选)。

本文件锁住任务 J 的改法:
  ① 键入生效的页面 → 直接成功, **不**进日历(省时);
  ② 键入不生效(真机那一态) → 自动落日历点选(同一格两击 + 整对读回) → 成功;
  ③ 两态都不成 → False + 细日志(输入框读回值 / URL / 表格行数与前 2 行), 绝不臆造日期;
  ④ 探测必须记录 readonly/placeholder/class(远程无真 DOM, 靠这几行适配)。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan as mt                           # noqa: E402
from merchant_report.platforms.meituan import MeituanAdapter                   # noqa: E402

mt.time.sleep = lambda *_: None          # 瞬时化(本文件全程假件驱动)


class FakeMouse:
    def __init__(self, page):
        self.page = page

    def click(self, x, y=None):
        self.page.mouse_clicks += 1
        if y is None:
            return
        for i, inp in enumerate(self.page.date_inputs()):
            if abs(inp["x"] - x) < 3 and abs(inp["y"] - y) < 3:
                self.page.focused = i
                return
        # 落到日格上 → 计数(成对协议要求点满 2 次页面才写日期)
        if self.page.cell_at(x, y) is not None:
            self.page.clicks += 1
            self.page.tick()
            return


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        p = self.page
        if key == "ControlOrMeta+a":
            p.buf = []
        elif key == "Enter":
            if p.type_sticks and p.focused is not None and len("".join(p.buf)) == 10:
                p.values[p.focused] = "".join(p.buf)
            p.buf = []
        elif key == "Escape":
            p.buf = []
            p.picker = False
        elif key == "Tab":
            p.focused = None

    def type(self, text, delay=None):
        self.page.buf.extend(list(text))


class FakeLocator:
    def __init__(self, page):
        self.page = page

    def click(self, timeout=None):
        self.page.clicks += 1
        self.page.tick()


class FakePage:
    """对账页假件。

    type_sticks=True   键入会被页面吃下(写进输入框) —— 「键入生效」态;
    type_sticks=False  键入被吞(真机 2026-10-14: 读回 ['', '']) —— 必须落日历兜底;
    day_cells / accepts 控制日历点选是否能把日期写进输入框(成对: 点满 2 次才写)。
    """

    def __init__(self, values=None, type_sticks=False, accepts=True, day_cells=1):
        self.values = values if values is not None else ["", ""]
        self.type_sticks = type_sticks
        self.accepts = accepts
        self.day_cells = day_cells
        self.clicks = 0
        self.mouse_clicks = 0
        self.buf = []
        self.focused = None
        self.picker = False
        self.url = "https://waimaieapp.meituan.com/finance/...billReconciliation.html"
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard(self)

    def date_inputs(self):
        return [{"x": 200.0, "y": 200.0, "cx": 200.0, "cy": 200.0, "v": self.values[0],
                 "ph": "开始日期", "tag": "start", "cls": "roo-input", "ro": False, "dis": False},
                {"x": 400.0, "y": 200.0, "cx": 400.0, "cy": 200.0, "v": self.values[1],
                 "ph": "结束日期", "tag": "end", "cls": "roo-input", "ro": False, "dis": False}]

    # 日历面板: 一个 2026-09 的 7x6 网格(足够覆盖本测的目标日 2026-09-30)
    def cells(self):
        if not self.picker:
            return []
        import datetime
        out = []
        for r in range(6):
            for c in range(7):
                d = datetime.date(2026, 9, 1) + datetime.timedelta(days=r * 7 + c)
                if d.month != 9:
                    continue
                out.append({"x": 100.0 + c * 30, "y": 120.0 + r * 24, "cx": 100.0 + c * 30 - 15,
                            "cy": 120.0 + r * 24 - 12, "w": 30.0, "h": 24.0, "d": str(d.day),
                            "cls": "", "anc": " roo-datepicker-body", "attr": d.isoformat(),
                            "dis": False, "other": False, "_date": d, "_tail": False,
                            "_panel": (2026, 9)})
        return out

    def panel_boxes(self):
        if not self.picker:
            return []
        return [{"head": "2026九月", "x": 100.0, "y": 120.0, "w": 210.0, "h": 144.0}]

    def cell_at(self, x, y):
        for c in self.cells():
            if abs(c["x"] - x) < 1.0 and abs(c["y"] - y) < 1.0:
                return c
        return None

    def tick(self):
        if self.accepts and self.clicks >= 2:
            self.values[0] = self.values[1] = "2026-09-30"

    def evaluate(self, js, arg=None):
        js = js or ""
        if "panels.push" in js:                       # _CAL_JS
            return {"panels": self.panel_boxes(), "cells": self.cells()}
        if "roo-datepicker-header" in js:             # _HEADS_JS / _MONTH_JS
            heads = [b["head"] for b in self.panel_boxes()]
            if "[class*=picker]" in js:
                return heads[0] if heads else None
            return heads
        if "pri = p =>" in js:                        # _bill_date_inputs
            return self.date_inputs()
        if "chevron" in js and "roo-icon" in js:      # 翻月(本测不需要真翻)
            return {"x": 10.0, "y": 10.0}
        if "data-hm-day" in js:                       # 标记日格
            return self.day_cells
        if "data-hm-custom" in js:                    # 「自定义」
            return 1
        if "document.body" in js and "innerText" in js:
            return "余额流水记录 推广费流水记录 自定义"
        return 0

    def locator(self, sel):
        if "data-hm-day" in sel:
            return FakeLocator(self)
        return FakeLocator(self)


def mk(page):
    ad = mt.MeituanAdapter.__new__(mt.MeituanAdapter)
    logs = []
    ad.page = page
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append((level, m))})()
    return ad, logs


def main():
    # ① 键入生效 → 直接成功(不进日历; 真机 10-14 不是这一态, 但别的环境可能是)
    p = FakePage(values=["", ""], type_sticks=True)
    ad, logs = mk(p)
    assert ad._set_bill_inputs(p, "2026-09-30") is True, logs
    assert p.values == ["2026-09-30", "2026-09-30"], p.values
    assert p.clicks == 0, "键入已生效就不该再点日历格子"
    assert any("日期控件" in m for _, m in logs), f"必须留下控件探测日志: {logs}"
    print("✅ ① 键入生效 → 直接成功(零日历点击) + 探测日志")

    # ② 键入不生效(真机那一态, 读回 ['','']) → 自动落日历点选 → 成功
    p2 = FakePage(values=["", ""], type_sticks=False, accepts=True)
    ad2, logs2 = mk(p2)
    # 打开日历: 假件里点输入框即开面板
    _orig_mouse = p2.mouse.click

    def _open(x, y=None):
        _orig_mouse(x, y)
        p2.picker = True

    p2.mouse.click = _open
    assert ad2._set_bill_inputs(p2, "2026-09-30") is True, logs2
    assert p2.values == ["2026-09-30", "2026-09-30"], p2.values
    assert p2.clicks >= 2, "日历兜底必须两侧各点一次目标日格(单击双侧)"
    assert any("键入3遍未收敛" in m for _, m in logs2), f"要留证: {logs2}"
    assert any("日历单击" in m and "已生效" in m for _, m in logs2), logs2
    print("✅ ② 键入3遍未收敛 → 日历单击双侧 → 生效")

    # ③ 两态都不成 → False + 细日志(输入框读回值), 绝不臆造日期
    p3 = FakePage(values=["", ""], type_sticks=False, accepts=False)
    ad3, logs3 = mk(p3)
    p3.mouse.click = _open_but_dead(p3)
    assert ad3._set_bill_inputs(p3, "2026-09-30") is False, logs3
    assert p3.values == ["", ""], "没拿到硬证据时绝不许把日期写进去"
    assert any("尝试" in m for _, m in logs3), logs3
    print("✅ ③ 键入+日历都不成 → False(留空) + 尝试日志")

    # ④ 格子点了不生效(结构改版/格子不可点) → 失败也要有取证, 不许静默, 更不许写日期
    p4 = FakePage(values=["", ""], type_sticks=False, accepts=False, day_cells=0)
    ad4, logs4 = mk(p4)
    p4.mouse.click = _open_but_dead(p4)
    assert ad4._set_bill_inputs(p4, "2026-09-30") is False, logs4
    assert any("尝试" in m for _, m in logs4), logs4
    assert p4.values == ["", ""], "没拿到硬证据时绝不许把日期写进去"
    print("✅ ④ 格子不生效 → False + 尝试日志(绝不臆造日期)")

    # ⑤ 契约: 探测字段 / 日历兜底 / 清场 / 翻月 都在实现里
    probe = inspect.getsource(MeituanAdapter._bill_date_inputs)
    assert "placeholder" in probe and "readOnly" in probe and "className" in probe, \
        "探测必须记 placeholder/readonly/class(远程排障靠它)"
    assert "开始" in probe and "结束" in probe, "要按 placeholder 认出起止两框"
    setter = inspect.getsource(MeituanAdapter._set_bill_inputs)
    assert "_type_bill_dates" in setter, "① 先试键入(任务 M4: 最多 3 遍收敛)"
    assert "_click_bill_day_once" in setter, \
        "② 键入不收敛 → 日历兜底走本页协议:**单击双侧**(不是流量页的同格两击)"
    assert "_click_same_cell_twice" not in setter, \
        "任务 M4: 对账页**不许**用流量页的同格两击(第 2 击会落到表格上误点)"
    click_once = inspect.getsource(MeituanAdapter._click_bill_day_once)
    assert "_open_bill_calendar" in setter and "_clear_picker" in setter, \
        "③ 日历兜底要能开面板/清场"
    assert "_nav_month_roo" in click_once, "翻月在「单击双侧」协议里(点框 → 翻月 → 单击日格)"
    assert "range(1, 3)" in setter, "日历兜底轮次要有上限"
    assert "_bill_typing_converged" in setter, "读回判定要复用统一的收敛函数(两侧都必须 == 目标日)"
    print("✅ ⑤ 契约: 探测 + 键入3遍 + 日历单击双侧 + 清场/翻月 + 轮次上限")

    # ⑥ 整流程重试 2 次(_read_bill_ledger)
    rd = inspect.getsource(MeituanAdapter._read_bill_ledger)
    assert "for attempt in (1, 2):" in rd, "对账页整流程必须重试 2 次"
    assert "_bill_once" in rd and "_bill_forensics" in rd, "失败要取证并重试"
    print("✅ ⑥ 对账页整流程重试 2 次 + 失败取证(_bill_forensics)")

    print("✅ 对账页日期(键入/日历两态 + 重试2次 + 取证)单测全部通过")


def _open_but_dead(page):
    def _c(x, y=None):
        page.mouse_clicks += 1
        page.picker = True          # 面板开, 但格子点了不生效(accepts=False 由 tick 控制)
    return _c


def _mk_diag_dir(page):
    """给假页挂一个临时 download_dir(现场包落盘用)。"""
    import tempfile
    d = tempfile.mkdtemp(prefix="mtm2bill-")
    page.tmpdir = d
    return d


def test_m4_bill_click_once_and_pack():
    """任务 M4: 对账页 ① **单击双侧**协议(不点同一格两次) ② 失败时现场包每 phase 一次
    ③ 三种失败 reason 可区分(键入3遍未收敛 / 日历兜底失败 / 日格禁用)。

    真机 2026-10-15 探针: 本页是**区间**控件 —— 正确姿势 = 点 box0 → 面板 → 翻月 → 单击目标日格
    (单击即落定、面板自关) → 再点 box1 → 同样单击。**同格两击是流量页协议, 对本页是错的**
    (第 2 击会落到表格上有误点风险)。所以本测试钉的是 M4 的新协议。
    """
    import os
    from merchant_report.platforms.meituan import MeituanAdapter as A

    # ① 键入3遍仍未收敛 → 落日历「单击双侧」→ 成功(两侧各点一次)
    p = FakePage(values=["", ""], type_sticks=False, accepts=True)
    ad, logs = mk(p)
    _orig = p.mouse.click
    cell_clicks = {"n": 0}

    def _open_and_count(x, y=None):
        _orig(x, y)
        p.picker = True
        if p.cell_at(x, y) is not None:
            cell_clicks["n"] += 1
    p.mouse.click = _open_and_count
    assert ad._set_bill_inputs(p, "2026-09-30") is True, logs
    assert any("单击双侧" in m and "已生效" in m for _, m in logs), logs
    assert any("键入3遍未收敛" in m for _, m in logs), logs
    print("✅ M4 对账页: 键入3遍未收敛 → 日历单击双侧 → 生效")

    # ② 日历兜底失败(格子点了不生效) → 现场包恰好一次 + reason 可区分
    p2 = FakePage(values=["", ""], type_sticks=False, accepts=False)
    ad2, logs2 = mk(p2)
    p2.mouse.click = _open_but_dead(p2)
    p2.frames = []                                 # 真 collect_diagnostic_pack 要读 page.frames
    d = _mk_diag_dir(p2)
    ad2.download_dir = d
    assert ad2._set_bill_inputs(p2, "2026-09-30") is False, logs2
    files = sorted(f for f in os.listdir(d) if f.startswith("diag_date-bill"))
    assert len(files) == 1, f"每 phase 每轮只写一次现场包: {files}"
    assert any("日期现场" in m for _, m in logs2), logs2
    # 现场包内 reason 必须是「日历兜底失败」(而不是含糊的"两轮全败")
    import json
    payload = json.load(open(os.path.join(d, files[0]), encoding="utf-8"))
    assert payload.get("reason") == "日历兜底失败", f"reason 要可区分: {payload.get('reason')}"
    assert "typing_readback" in payload, '现场包要带键入那一路的读回值(区分「键入未收敛」)'
    print(f"✅ M4 对账页失败: reason={payload.get('reason')} + 现场包一次({files[0]})")

    # ③ 取证自身抛异常 → 主流程照常 False, 不崩
    import merchant_report.diagnose as diag
    orig = diag.collect_diagnostic_pack
    diag.collect_diagnostic_pack = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        p3 = FakePage(values=["", ""], type_sticks=False, accepts=False, day_cells=0)
        ad3, logs3 = mk(p3)
        p3.mouse.click = _open_but_dead(p3)
        assert ad3._set_bill_inputs(p3, "2026-09-30") is False, logs3
    finally:
        diag.collect_diagnostic_pack = orig
    print("✅ M4 取证异常绝缘: collect 抛异常 → 主流程照常返回 False")

    # ④ 契约: 挂点齐全(面板未能打开 / 兜底失败) + 三种 reason 可区分 + 陈旧错字消失
    setter = inspect.getsource(A._set_bill_inputs)
    assert "_date_pack" in setter, "对账页失败必须写日期现场包"
    assert setter.count("_date_pack") >= 2, "两个挂点: 面板未能打开 + 日历兜底失败"
    assert "_CAL_FAIL" in setter and "_DISABLED" in setter, \
        "三种 reason 可区分: 键入未收敛(独立日志) / 日历兜底失败 / 日格禁用"
    assert "日格禁用" in inspect.getsource(A._click_bill_day_once), "日格禁用要就地判(不点)"
    import re as _re
    assert not _re.search(r"面[0-9]|面\{", inspect.getsource(A)), "「面」分隔符错字必须清零"
    assert "{m:02d}-{day:02d}" in inspect.getsource(A._mark_day_cell), "desc 改为常规 - 分隔"
    print("✅ M4 对账页契约: 两个挂点 + 三种 reason 可区分 + 错字清零")


if __name__ == "__main__":
    main()
    test_m4_bill_click_once_and_pack()
