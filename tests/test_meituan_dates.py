# -*- coding: utf-8 -*-
"""美团下载页日期(roo 双面板)单测: 中文数字月解析 + 双面板「重复日数字」对抗 + 翻月读回验证。

真机事实(2026-10-09): 面板头 innerText 形如「2026十月」(中文数字); 可点的日子 <td> 可能
**没有 class**(禁用格是 day disabled); 翻月按钮 = i.roo-icon-chevron-left/right-new。

真机事故(2026-10-14, diag_download_20261014-115841): 要设 09-05, 实际点中 **10-05**,
读回 gate 拦下并主动中止(没出错下载)。根因: 双面板日历里"日数字"必然重复 —— 邻月尾巴格渲染同一
个数字; 九月面板 [九月 (含 10 月尾巴)][十月 (含 11 月尾巴)] 里 "5" 至少出现 4 次, 而旧实现
「收集全部可见格 → 按 x 取最左」把**九月面板最后一行的 10/5 尾巴格**当成 9/5 点了。
本文件用**纯 Python 假件**(模拟 evaluate 契约, 不联网不开浏览器)锁住修复后的四条纪律:
  ① 面板感知(表头↔body 配对, 全部可见面板) ② 目标月可见就绝不翻月, 候选限定在该面板 bbox 内
  ③ 每个候选**点后读回对应输入框**, 不对就换下一个候选 ④ 翻月按 min/max 判方向 + 点后读回面板月份验证。
"""
import datetime
import os
import re
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_HERE, "kernel"))

from merchant_report.platforms import meituan as mt                          # noqa: E402
from merchant_report.platforms.meituan import MeituanAdapter                 # noqa: E402

mt.time.sleep = lambda *_: None      # 瞬时化(本文件全程假件驱动; 真机节奏由真机验收负责)


class FakePage:
    """表头文本假件(旧用例保留): evaluate 一律返回给定文本。"""

    def __init__(self, text=None, raise_exc=False):
        self._text = text
        self._raise = raise_exc

    def evaluate(self, _js, *a):
        if self._raise:
            raise RuntimeError("boom")
        return self._text


# ---------------------------------------------------------------- 双面板日历假件
_CN_NAME = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
            7: "七", 8: "八", 9: "九", 10: "十", 11: "十一", 12: "十二"}
_COLW, _ROWH, _HDR = 42, 30, 32


def _grid(y, m):
    """6 行 × 7 列(周一起)的月网格, 含上/下月尾巴格 —— 与真机 page_text 同构。"""
    first = datetime.date(y, m, 1)
    start = first - datetime.timedelta(days=first.weekday())
    out = []
    for i in range(42):
        dt = start + datetime.timedelta(days=i)
        out.append({"row": i // 7, "col": i % 7, "date": dt,
                    "tail": (dt.year, dt.month) != (y, m)})
    return out


def _cell_center(bx, by, row, col):
    return (bx + col * _COLW + _COLW / 2.0, by + _HDR + row * _ROWH + _ROWH / 2.0)


def _real_center(y, m, day, panels):
    """目标面板里 day 的**当月格**(非尾巴)中心坐标; 找不到返回 None。"""
    for (py, pm, bx, by) in panels:
        if (py, pm) == (y, m):
            for c in _grid(y, m):
                if c["date"].day == day and not c["tail"]:
                    return _cell_center(bx, by, c["row"], c["col"])
    return None


class _Mouse:
    def __init__(self, page):
        self.page = page

    def click(self, x, y=None):
        self.page.click(x, y)


class CalPage:
    """双面板日历假件(模拟 evaluate 契约 + 真鼠标坐标点击)。

    panels: [(年, 月, 面板左 x, 面板上 y)] —— **顺序 = 可见/DOM 顺序**(测左右顺序健壮性时换序)。
    行为开关(默认都开):
      attrs=True            非尾巴格带 data-date 属性(可解析日期 → 候选排序的第一优先级)
      flip_works=True       点 chevron 时真翻月; False = 点了没反应(验证读回闸门)
      dead_chevron=False    True = 同方向第一个 chevron 是死的(必须换另一个候选)
      close_after_first_cell=False  点第一格后面板收起(验证"选择器关了要重开")
      keep_open_after_end=True      设完结束日后面板是否收起(默认收起, 同真机)
    """

    def __init__(self, panels, values=None, attrs=True, flip_works=True, dead_chevron=False,
                 close_after_first_cell=False, keep_open_after_end=False, head_fmt=None):
        self.panels = list(panels)
        self.inputs = [{"x": 1000.0, "y": 60.0, "v": (values or ["", ""])[0]},
                       {"x": 1200.0, "y": 60.0, "v": (values or ["", ""])[1]}]
        self.attrs = attrs
        self.flip_works = flip_works
        self.dead_chevron = dead_chevron
        self.close_after_first_cell = close_after_first_cell
        self.keep_open_after_end = keep_open_after_end
        self.head_fmt = head_fmt or (lambda y, m: "%d%s" % (y, _CN_NAME[m]))
        self.picker = True
        self.armed = None                 # 最近一次点中的输入框下标(0=开始, 1=结束)
        self.cell_clicks = []             # 每次落到日期格上的点击(含真实日期/是否尾巴/armed)
        self.chevron_clicks = []          # 每次翻月点击(fwd, 有效?)
        self.input_clicks = []
        self.mouse = _Mouse(self)

    # ---- 只读视图 ----
    def cells(self):
        if not self.picker:
            return []
        out = []
        for (y, m, bx, by) in self.panels:
            for c in _grid(y, m):
                x, y2 = _cell_center(bx, by, c["row"], c["col"])
                out.append({"x": x, "y": y2, "cx": x - _COLW / 2.0, "cy": y2 - _ROWH / 2.0,
                            "w": float(_COLW), "h": float(_ROWH), "d": str(c["date"].day),
                            "cls": "day other" if c["tail"] else "",
                            "anc": " roo-datepicker-body",
                            "attr": ("" if c["tail"] else c["date"].isoformat()) if self.attrs else "",
                            "dis": False, "other": c["tail"],
                            "_date": c["date"], "_tail": c["tail"], "_panel": (y, m)})
        return out

    def panel_boxes(self):
        if not self.picker:
            return []
        return [{"head": self.head_fmt(y, m), "x": float(bx), "y": float(by + _HDR),
                 "w": float(_COLW * 7), "h": float(_ROWH * 6)} for (y, m, bx, by) in self.panels]

    def cell_at(self, x, y):
        for c in self.cells():
            if abs(c["x"] - x) < 1.0 and abs(c["y"] - y) < 1.0:
                return c
        return None

    def chevrons(self):
        if not self.picker:
            return []
        out = []
        for (y, m, bx, by) in self.panels:
            out.append({"fwd": False, "x": float(bx - 14), "y": float(by + _HDR - 16)})
            out.append({"fwd": True, "x": float(bx + _COLW * 7 + 14), "y": float(by + _HDR - 16)})
        out.sort(key=lambda c: c["x"])
        if self.dead_chevron:                     # 同方向第一个 chevron 是死的
            for fwd in (False, True):
                seen = 0
                for c in out:
                    if c["fwd"] == fwd:
                        seen += 1
                        if seen == 1:
                            c["dead"] = True
        return out

    # ---- evaluate 契约(按 JS 特征串分派, 与 meituan.py 里的常量一一对应) ----
    def evaluate(self, js, arg=None):
        js = js or ""
        if "panels.push" in js:                               # _CAL_JS(必须先判: 它含 \d{1,2})
            return {"panels": self.panel_boxes(), "cells": self.cells()}
        if ".roo-datepicker-body td" in js:                   # 旧 _DAY_JS(仅兜底路径)
            hit = [c for c in self.cells() if c["d"] == str(arg) and not c["dis"]]
            if not hit:
                return None
            hit.sort(key=lambda c: c["cx"])                   # 真机的 bug: 取最左
            return {"x": hit[0]["x"], "y": hit[0]["y"]}
        if "chevron-right-new" in js:                         # 同向 chevron 候选
            return [{"x": c["x"], "y": c["y"], "cx": c["x"] - 14}
                    for c in self.chevrons() if c["fwd"] == bool(arg)]
        if "roo-datepicker-header" in js:                     # _HEADS_JS / _MONTH_JS
            heads = [b["head"] for b in self.panel_boxes()]
            if "[class*=picker]" in js:                       # _MONTH_JS: 单个字符串
                return heads[0] if heads else None
            return heads
        if "isd =" in js:                                     # _DATES_JS
            return [dict(x=i["x"], y=i["y"], cx=i["x"], cy=i["y"], v=i["v"]) for i in self.inputs]
        return 0

    # ---- 页面行为 ----
    def click(self, x, y=None):
        for i, inp in enumerate(self.inputs):
            if abs(inp["x"] - x) < 3 and abs(inp["y"] - y) < 3:
                self.input_clicks.append(i)
                self.armed = i
                self.picker = True                            # 点输入框 = 选择器打开
                return
        for c in self.chevrons():
            if abs(c["x"] - x) < 3 and abs(c["y"] - y) < 3:
                self.click_chevron(c)
                return
        cell = self.cell_at(x, y)
        if cell:
            self.click_cell(cell)

    def click_chevron(self, chev):
        before = [(y, m) for (y, m, _x, _y) in self.panels]
        ok = self.flip_works and not chev.get("dead")
        if ok:                                                # roo: 两个面板一起移动一个月
            step = 1 if chev["fwd"] else -1
            moved = []
            for (y, m) in before:
                nm, ny = m + step, y
                if nm == 0:
                    nm, ny = 12, y - 1
                elif nm == 13:
                    nm, ny = 1, y + 1
                moved.append((ny, nm))
            self.panels = [(y, m, bx, by) for (y, m), (_py, _pm, bx, by)
                           in zip(moved, self.panels)]
        self.chevron_clicks.append({"fwd": bool(chev["fwd"]), "ok": ok})

    def click_cell(self, cell):
        self.cell_clicks.append({"date": cell["_date"].isoformat(), "tail": cell["_tail"],
                                 "x": cell["x"], "y": cell["y"], "armed": self.armed,
                                 "panel": cell["_panel"]})
        if self.armed == 1 and not self.keep_open_after_end:
            self.picker = False                               # 选完结束日 → 选择器收起
        elif self.close_after_first_cell:
            self.picker = False                               # 模拟"点一下面板就被关掉"
        self.inputs[0 if self.armed != 1 else 1]["v"] = cell["_date"].isoformat()


def _mk(page):
    ad = MeituanAdapter.__new__(MeituanAdapter)
    logs = []
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append((level, m))})()
    return ad, logs


def _clicks(page, armed):
    """armed=0 取"设开始日"的点击(直接调 _click_day 时 armed 可能是 None, 也算开始)。"""
    return [c for c in page.cell_clicks if (c["armed"] == 1) == (armed == 1)]


# ---------------------------------------------------------------- 旧用例(保留)
def main():
    ad = MeituanAdapter.__new__(MeituanAdapter)
    cases = [
        ("2026十月", (2026, 10)),
        ("2026九月", (2026, 9)),
        ("2026八月", (2026, 8)),
        ("2026 十一月", (2026, 11)),
        ("2026\n十二月", (2026, 12)),
        ("2026一", (2026, 1)),
        ("2026年10月", (2026, 10)),
        ("2026-09", (2026, 9)),
        ("2026/3", (2026, 3)),
        ("2026", None),
        ("加载中", None),
        ("", None),
    ]
    for text, want in cases:
        got = ad._panel_month(FakePage(text))
        assert got == want, f"面板头 {text!r}: 期望 {want}, 实际 {got}"
    assert ad._panel_month(FakePage(raise_exc=True)) is None
    assert ad._panel_month(FakePage(None)) is None
    print("✅ 表头解析(中文数字/年-月/裸年)全部通过")

    for text, want in (("2026十月", (2026, 10)), ("2026年10月", (2026, 10)), ("2026-10", (2026, 10))):
        assert ad._parse_month(text) == want, (text, ad._parse_month(text))
    assert ad._parse_month(None) is None and ad._parse_month("加载中") is None
    print("✅ _parse_month 复用解析(中文数字月 / 2026年10月 / 2026-10)通过")

    src = open(os.path.join(_HERE, "kernel/merchant_report/platforms/meituan.py"), encoding="utf-8").read()
    for needle in ("chevron-left-new", "chevron-right-new", ".roo-datepicker-body td",
                   "_CN_MONTH", "roo-datepicker-header", "indexOf('disabled')"):
        assert needle in src, f"源码缺少关键标志: {needle}"
    assert "td.day a" not in src, "旧选择器 td.day a 残留(可点格可能无 class)"
    print("✅ 关键选择器守卫通过")

    # _click_day: 面板月份不对→翻月→点格; 用 fake 面板跟踪月份驱动(不碰浏览器)
    class _Mouse2:
        def click(self, x, y):
            pass

    class FPage:
        def __init__(self, month):
            self.month = month
            self.mouse = _Mouse2()

        def evaluate(self, js, *a):
            if "chevron" in js:
                return {"x": 1, "y": 2}
            if "roo-datepicker-body td" in js:
                return {"x": 1, "y": 2} if self.month == (2026, 9) else None
            return None

    class Ad3(MeituanAdapter):
        def __init__(self):
            self.nav_calls = 0

        def _panel_month(self, page):
            return page.month

        def _cal_state(self, page):                # 无面板快照 → 走旧路径
            return [], []

        def _nav_month(self, page, forward):
            assert not forward, "目标(2026-09)在更早月份, 不该往后翻"
            self.nav_calls += 1
            page.month = (2026, 9)
            return True

    a3 = Ad3()
    p = FPage((2026, 10))
    assert a3._click_day(p, "2026-09-24") is True, "应从 10 月翻到 9 月并点中"
    assert a3.nav_calls == 1, f"应恰好翻月 1 次, 实际 {a3.nav_calls}"
    a3b = Ad3()
    p2 = FPage((2026, 9))
    assert a3b._click_day(p2, "2026-09-24") is True, "已在目标月应直接点中"
    assert a3b.nav_calls == 0, "已在目标月不该翻月"
    print("✅ _click_day 旧路径(无面板快照)月份驱动通过")

    print("✅ 全部通过 (美团日期控件修复·旧用例)")




# ---------------------------------------------------------------- 新增: 双面板对抗
def test_report_scenario_9_5():
    """① 报告场景复现: 双面板 [九月(含 10 月尾巴)][十月(含 11 月尾巴)], 目标 2026-09-05。

    真机(diag_download_20261014-115841): 旧实现"取最左的 5"点中了**九月面板最后一行的 10/5 尾巴格**
    → 读回 ['2026-10-05','2026-10-05'] → 中止。修复后必须点中九月面板里的 9/5。
    """
    page = CalPage([(2026, 9, 100, 200), (2026, 10, 460, 200)], values=["2026-09-01", "2026-09-05"])
    ad, logs = _mk(page)
    ad._set_date_range(page, "2026-09-05", "2026-09-05")   # 不抛异常 = 末端读回 gate 通过
    assert page.inputs[0]["v"] == "2026-09-05" and page.inputs[1]["v"] == "2026-09-05", page.inputs
    assert page.chevron_clicks == [], f"目标月在可见面板里, 绝不该翻月: {page.chevron_clicks}"
    want = _real_center(2026, 9, 5, [(2026, 9, 100, 200), (2026, 10, 460, 200)])
    starts = _clicks(page, 0)
    assert starts, f"开始日必须有点击记录: {page.cell_clicks}"
    assert (starts[0]["x"], starts[0]["y"]) == want, \
        f"必须点在九月面板的 9/5 格 {want}, 实际 {(starts[0]['x'], starts[0]['y'])}"
    assert all(c["date"] == "2026-09-05" and not c["tail"] for c in starts), starts
    # 反向确认: 本假件确实复现了真机的陷阱 —— "5" 的最左格是**邻月尾巴格**(旧实现就是点它)
    probe = CalPage([(2026, 9, 100, 200), (2026, 10, 460, 200)], values=["", ""])
    naive = min((c for c in probe.cells() if c["d"] == "5"), key=lambda c: c["cx"])
    assert naive["_date"].isoformat() == "2026-10-05" and naive["_tail"], \
        f"假件没复现真机陷阱(最左的 5 应是 10/5 尾巴格): {naive}"
    assert (naive["x"], naive["y"]) != want, "最左格(10/5 尾巴)不能被当成目标格"
    assert any("📅 日期区间 = 2026-09-05 ~ 2026-09-05" in m for _l, m in logs), logs
    print("✅ ① 报告场景: 双面板里点中九月面板的 9/5(不是最左的 10/5 尾巴格), 读回一致")


def test_duplicate_cell_adversarial():
    """② 重复格对抗: 第一个候选点完读回 10-05(错) → 循环必须**继续**试下一个候选并最终成功。"""
    page = CalPage([(2026, 9, 100, 200), (2026, 10, 460, 200)], values=["2026-10-05", "2026-10-05"])
    ad, logs = _mk(page)

    def _fake(grid_page, y, m, d):                     # 把 10/5 尾巴格排在候选最前(模拟排序被误导)
        cells = [c for c in grid_page.cells()
                 if c["d"] == str(d) and c["_panel"] == (y, m) and not c["dis"]]
        return sorted(cells, key=lambda c: 0 if c["_tail"] else 1)

    ad._day_candidates = _fake
    assert ad._click_day(page, "2026-09-05", "start") is True, logs
    starts = _clicks(page, 0)
    assert [c["tail"] for c in starts] == [True, False], f"应先点尾巴格(读回 10-05)再换真格: {starts}"
    assert page.inputs[0]["v"] == "2026-09-05", page.inputs
    print("✅ ② 重复格对抗: 第一个候选(10/5 尾巴)读回不对 → 循环继续 → 第二个候选成功")


def test_reopen_picker_when_closed():
    """②b 面板被点收起时, 要重新点开输入框才能接着试下一个候选(不许因此空转到 False)。"""
    page = CalPage([(2026, 9, 100, 200), (2026, 10, 460, 200)], values=["2026-08-01", "2026-08-01"],
                   close_after_first_cell=True)
    ad, logs = _mk(page)

    def _fake(grid_page, y, m, d):
        cells = [c for c in grid_page.cells()
                 if c["d"] == str(d) and c["_panel"] == (y, m) and not c["dis"]]
        return sorted(cells, key=lambda c: 0 if c["_tail"] else 1)

    ad._day_candidates = _fake
    assert ad._click_day(page, "2026-09-05", "start") is True, logs
    assert page.input_clicks, "面板被收起后必须重新点开输入框"
    assert page.inputs[0]["v"] == "2026-09-05", page.inputs
    print("✅ ②b 选择器被关掉 → 重新点开 → 继续验证候选")


def test_nav_month_verified_and_direction():
    """③ 翻月验证: 目标月不可见 → 方向靠 min/max 判(不假设左右顺序) + 点后读回面板月份;
    翻月无效 → 有限次后返回 False(不抛异常、不死循环)。"""
    page = CalPage([(2026, 10, 100, 200), (2026, 11, 460, 200)], values=["2026-11-13", "2026-11-13"])
    ad, logs = _mk(page)
    assert ad._click_day(page, "2026-09-05", "start") is True, logs
    assert page.chevron_clicks, "跨月目标必须触发翻月"
    assert all(c["fwd"] is False for c in page.chevron_clicks), \
        f"目标比最小可见月(2026-10)更早 → 只许往前翻: {page.chevron_clicks}"
    start = _clicks(page, 0)[0]
    assert start["date"] == "2026-09-05" and not start["tail"], start
    assert start["panel"] == (2026, 9), start

    # 面板顺序颠倒(十月在前, 七月在后): 方向仍必须用 min/max 判 → 仍是往前翻
    page2 = CalPage([(2026, 10, 100, 200), (2026, 7, 460, 200)], values=["2026-07-01", "2026-07-01"])
    ad2, logs2 = _mk(page2)
    assert ad2._click_day(page2, "2026-09-05", "start") is True, logs2
    assert all(c["fwd"] is False for c in page2.chevron_clicks), page2.chevron_clicks

    # 往后的方向: 目标比最大可见月更晚
    page3 = CalPage([(2026, 9, 100, 200), (2026, 10, 460, 200)], values=["2026-09-01", "2026-09-01"])
    ad3, logs3 = _mk(page3)
    assert ad3._click_day(page3, "2026-11-20", "start") is True, logs3
    assert all(c["fwd"] is True for c in page3.chevron_clicks), page3.chevron_clicks

    # 翻月无效(点了没反应) → 有限次后 False, 不抛异常
    page4 = CalPage([(2026, 10, 100, 200), (2026, 11, 460, 200)], values=["2026-11-01", "2026-11-01"],
                    flip_works=False)
    ad4, logs4 = _mk(page4)
    assert ad4._click_day(page4, "2026-09-05", "start") is False, logs4
    assert len(page4.chevron_clicks) < 400, f"翻月要有次数上限: {len(page4.chevron_clicks)}"
    assert page4.cell_clicks == [], f"翻月没到位时不许退而点格子: {page4.cell_clicks}"
    print("✅ ③ 翻月: 方向 min/max 判 + 点后读回验证; 无效翻月 → False(不抛异常/不死循环)")


def test_dead_chevron_fallback():
    """③b 同方向第一个 chevron 点不动(读回没变) → 换同方向另一个候选; 都不行 → False。"""
    page = CalPage([(2026, 10, 100, 200), (2026, 11, 460, 200)], values=["2026-11-01", "2026-11-01"],
                   dead_chevron=True)
    ad, logs = _mk(page)
    assert ad._click_day(page, "2026-09-05", "start") is True, logs
    assert any(not c["ok"] for c in page.chevron_clicks), "应有一次无效的 chevron 点击"
    assert any(c["ok"] for c in page.chevron_clicks), "换到另一个 chevron 后必须成功"
    print("✅ ③b 死 chevron → 换同向另一个候选 → 成功")


def test_candidate_ranking():
    """候选排序(软化): 属性精确匹配最优先; 明确是别的日期的属性靠后; 尾巴格降权。"""
    page = CalPage([(2026, 9, 100, 200), (2026, 10, 460, 200)], values=["", ""])
    ad, _ = _mk(page)
    cands = ad._day_candidates(page, 2026, 9, 5)
    assert cands, "九月面板里应有 day=5 的候选"
    assert all(not c["other"] for c in cands[:1]), cands[:1]
    assert all(c["cx"] < 460 for c in cands), "候选格必须限定在九月面板 bbox 内"
    # 无属性版本: 排序降级为"行列顺序 + 尾巴降权", 尾巴格仍不得排在非尾巴之前
    page2 = CalPage([(2026, 9, 100, 200)], values=["", ""], attrs=False)
    cands2 = ad._day_candidates(page2, 2026, 9, 5)
    assert cands2 and not cands2[0]["other"], f"尾巴格不许排最前: {cands2[:1]}"
    print("✅ 候选排序: bbox 限定 + 尾巴降权 + 属性优先(仅排序, 不替代读回验证)")


def main_all():
    main()
    test_report_scenario_9_5()
    test_duplicate_cell_adversarial()
    test_reopen_picker_when_closed()
    test_nav_month_verified_and_direction()
    test_dead_chevron_fallback()
    test_candidate_ranking()
    print("✅ 全部通过 (任务G: 双面板重复日数字 + 点后读回 + 翻月验证)")


if __name__ == "__main__":
    main_all()
