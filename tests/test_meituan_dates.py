# -*- coding: utf-8 -*-
"""美团下载页日期(roo 双面板)单测: 中文数字月解析 + 双面板「重复日数字」对抗 + 翻月读回验证。

真机事实(2026-10-09): 面板头 innerText 形如「2026十月」(中文数字); 可点的日子 <td> 可能
**没有 class**(禁用格是 day disabled); 翻月按钮 = i.roo-icon-chevron-left/right-new。

⚠️⚠️ **本文件的交互模型 = 真机语义(2026-10-14 v2 重写; 这是任务 G 翻车的真正原因)**:
  **单击日期格只置"待定起点", 输入框不变; 再单击任一格才完成区间 [pending, clicked] 并写两个输入框;
  同格两击 = [d, d]。** 点输入框 = 打开/切换活动侧, 不打扰待定状态。
  真机原文(流量页 2026-10-08 证实, 下载页 2026-10-14 再现): "点 1 次后仍 10-07, 点 2 次后变 10-06
  且两个输入框都填上"。**v1 的假件把"单击格"建成"直接写输入框"** → 模型错了 → 测试全绿而真机翻车。
  一切"每击即验证 / 不对就换候选"的写法在这个模型下必然把区间凑成跨月错对。见 `CalPage.click_cell`。

真机事故(两次, 两次都没让错区间下载 —— 读回 gate 拦下了):
  · diag_download_20261014-115841: 要 09-05 却设成 **10-05**。根因: 双面板日历里"日数字"必然重复
    (邻月尾巴格渲染同一个数字; 九月面板最后一行第 1 列的 10/5 就是最左的 "5"), 旧实现
    「收集全部可见格 → 按 x 取最左」把**尾巴格**当成 9/5 点了。
  · diag_download_20261014-150407: 要 09-08 却设成 **10-08**。根因: "每击即验证"的连锁 —— 第 1 击
    (正确格)读回没变(这是**正常**的待定语义) → 误判失败 → 换到尾巴格 → 那一击凑出跨月区间
    [09-08, 10-08] → 单框验证读到 input[0]=09-08 就"通过"了 → 之后 45s 都在脏区间里重凑这个错对。

本文件锁住的纪律(任务 H v2): ① 面板感知 + 候选限定在目标面板 bbox 内(月感知, 尾巴降权)
  ② 一次区间必须由**同一对格子**的两击完成(序列内零验证零换格) ③ 整对读回(read == [df, dt])才算成
  ④ 失败 Esc 清场重来, 绝不在脏状态里试探 ⑤ 翻月按 min/max 判方向 + 点后读回月份验证
  ⑥ 墙钟有限、宁可快速失败。
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
    """双面板日历假件 v2(模拟 evaluate 契约 + 真鼠标坐标点击)。

    ⚠️⚠️ **交互模型 v2(以真机语义为准 —— v1 的假件在这里就建错了, 所以测试全绿而真机翻车)**:
      单击日期格 = 置"待定起点"(**输入框不变**); **再单击任一格 = 完成区间 [pending, clicked]**
      (写两个输入框); 同格两击 = [d, d]。点输入框 = 打开/切换活动侧, **不打扰待定状态**。
      真机原文(流量页 2026-10-08, 下载页 2026-10-14 再现):
      "点 1 次后仍 10-07, 点 2 次后变 10-06 且两个输入框都填上"。

    panels: [(年, 月, 面板左 x, 面板上 y)] —— **顺序 = 可见/DOM 顺序**(测左右顺序健壮性时换序)。
    行为开关:
      attrs=True            非尾巴格带 data-date 属性(可解析日期 → 候选排序的第一优先级)
      flip_works=True       点 chevron 时真翻月; False = 点了没反应(验证读回闸门)
      dead_chevron=False    True = 同方向第一个 chevron 是死的(必须换另一个候选)
      single_click_completes=False  组件另一版本语义: **单击格直接完成 [d, d]**(没有待定态)
      close_after_complete=False    完成即关面板(默认**保持打开**, 对抗性更强)
      inert_cell_dates=()   这些日期的格"点了不生效"(待定与区间都不动), 用于对抗用例
      pending_only_dates=() 这些日期的格"点了只置待定"(第 2 击也完不成区间), 用于对抗用例
      first_cell_closes=False       点第一格面板就收起(验证"选择器关了要重开")
      picker_open=True      初始面板是否已展开(False = 像真机刚打开下载页那样, 需要先点开始框)
    """

    def __init__(self, panels, values=None, attrs=True, flip_works=True, dead_chevron=False,
                 single_click_completes=False, close_after_complete=False, inert_cell_dates=(),
                 pending_only_dates=(), first_cell_closes=False, head_fmt=None, picker_open=True):
        self.panels = list(panels)
        self.inputs = [{"x": 1000.0, "y": 60.0, "v": (values or ["", ""])[0]},
                       {"x": 1200.0, "y": 60.0, "v": (values or ["", ""])[1]}]
        self.attrs = attrs
        self.flip_works = flip_works
        self.dead_chevron = dead_chevron
        self.single_click_completes = single_click_completes
        self.close_after_complete = close_after_complete
        self.inert_cell_dates = set(inert_cell_dates)
        self.pending_only_dates = set(pending_only_dates)
        self.first_cell_closes = first_cell_closes
        self.head_fmt = head_fmt or (lambda y, m: "%d%s" % (y, _CN_NAME[m]))
        self.picker = bool(picker_open)
        self.armed = None                 # 最近一次点中的输入框下标(0=开始, 1=结束)
        self.pending = None               # 待定起点(None = 没有待定) —— 真机第 1 击只置它, 不写输入框
        self.cell_clicks = []             # 每次落到日期格上的点击(含真实日期/是否尾巴)
        self.chevron_clicks = []          # 每次翻月点击(fwd, 有效?)
        self.input_clicks = []
        self.esc_count = 0                # Esc 清场次数(协议"不许在脏状态里试探"的取证)
        self.pair_reads = []              # 每次"完成区间"写出的整对(取证)
        self.cross_month_pairs = []       # 跨月对(现场事故 [09-08, 10-08] 就是它) —— 协议必须**从不产生**
        self.events = []                  # 全局事件序(输入框/格/chevron), 用于核对 4 步序列形状
        self.opened_by_click = []         # 点输入框前"面板本来是关着"的记录(决定协议是否该真的击发)
        self.hook = None                  # (kind, *args) -> None: 每次事件后的注入点(对抗用例用)
        self.mouse = _Mouse(self)
        self.keyboard = _Keyboard(self)

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
                self.opened_by_click.append(not self.picker)   # 击发前面板本来关着?
                self.picker = True                            # 点输入框 = 选择器打开(不动 pending)
                self.events.append(("input", i))
                self._hook("input", i)
                return
        for c in self.chevrons():
            if abs(c["x"] - x) < 3 and abs(c["y"] - y) < 3:
                self.click_chevron(c)
                return
        cell = self.cell_at(x, y)
        if cell:
            self.click_cell(cell)

    def _hook(self, kind, *args):
        if self.hook:
            self.hook(kind, *args)

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
        self.events.append(("chevron", bool(chev["fwd"]), ok))
        self._hook("chevron", bool(chev["fwd"]), ok)

    def click_cell(self, cell):
        """真机语义: 第 1 击=置待定起点(输入框不变); 第 2 击=完成区间(写两个输入框)。"""
        iso = cell["_date"].isoformat()
        self.cell_clicks.append({"date": iso, "tail": cell["_tail"], "x": cell["x"], "y": cell["y"],
                                 "armed": self.armed, "panel": cell["_panel"]})
        self.events.append(("cell", iso, cell["_panel"]))
        self._hook("cell", iso, cell["_panel"])
        if self.first_cell_closes and len(self.cell_clicks) == 1:
            self.picker = False
            return
        if iso in self.inert_cell_dates:                  # 这一格"不生效": 连待定都不置
            return
        if iso in self.pending_only_dates:                # 这一格"点了只置待定": **两击都只置待定**
            self.pending = cell["_date"]
            return
        if self.single_click_completes:                   # 组件另一版本: 单击即成
            self.inputs[0]["v"] = self.inputs[1]["v"] = iso
            self.pair_reads.append([iso, iso])
            self.pending = None
            if self.close_after_complete:
                self.picker = False
            return
        if self.pending is None:                          # ← 第 1 击: 只置待定, **输入框完全不变**
            self.pending = cell["_date"]
            return
        s, e = self.pending, cell["_date"]                # ← 第 2 击: 完成区间 [待定, 本次]
        if e < s:
            s, e = e, s
        self.inputs[0]["v"], self.inputs[1]["v"] = s.isoformat(), e.isoformat()
        self.pair_reads.append([s.isoformat(), e.isoformat()])
        if (s.year, s.month) != (e.year, e.month):
            self.cross_month_pairs.append([s.isoformat(), e.isoformat()])
        self.pending = None
        if self.close_after_complete:
            self.picker = False


class _Keyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        if key in ("Escape", "Esc"):
            self.page.picker = False                      # Esc = 关面板 + 丢掉待定(输入框值保留)
            self.page.pending = None
            self.page.esc_count += 1

    def type(self, _text, delay=0):
        pass


def _mk(page):
    ad = MeituanAdapter.__new__(MeituanAdapter)
    logs = []
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append((level, m))})()
    return ad, logs


def _logs(logs):
    """成对尝试日志行(每次尝试一行, 成败都记): 「🖱 日期尝试{轮}: {描述} → 读回 [...]+」。"""
    return [m for _l, m in logs if "🖱 日期尝试" in m]


_PANELS_9_10 = [(2026, 9, 100, 200), (2026, 10, 460, 200)]


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

    # 翻月方向(成对协议里 "目标月不在可见面板" → 按 min/max 判方向翻月, 翻到位再走 4 步序列)。
    # 旧实现有 "无面板快照 → _try_day 耐心重试" 的兜底分支, v2 已删除(成对协议要求面板快照可用,
    # 拿不到就交上层清场重来 —— 见 _pair_flow 的 `if not months: return False`)。这里改用假件驱动。
    class _Event:
        def __init__(self, box):
            self.box = box

    class FPage2:
        """只有 head/chevron 的极简页面: 点 chevron 就翻月; 点格记事件。"""

        def __init__(self, panels):
            self.panels = list(panels)
            self.hits = []
            self.mouse = type("M", (), {"click": lambda _s, x, y: self.click(x, y)})()

        def click(self, x, y):
            if y < 100:                                        # 头部 chevron 行
                step = 1 if x > 500 else -1
                self.panels = [((y2, 1 if m2 + step == 13 else (12 if m2 + step == 0 else m2 + step)))
                               for (y2, m2) in self.panels]
                return
            self.hits.append((x, y))

        def evaluate(self, js, *a):
            js = js or ""
            if "chevron-right-new" in js:
                return [{"x": 900.0 if a[0] else 10.0, "y": 50.0, "cx": 10.0}]
            if "roo-datepicker-header" in js:
                return [self.head_fmt(y, m) for (y, m) in self.panels]
            if "panels.push" in js:
                return {"panels": [], "cells": []}
            return None

        head_fmt = staticmethod(lambda y, m: "%d%s" % (y, _CN_NAME[m]))

    ad4 = MeituanAdapter.__new__(MeituanAdapter)
    ad4.sink = type("S", (), {"log": lambda *a, **k: None})()
    p4 = FPage2([(2026, 10), (2026, 11)])
    assert ad4._nav_month(p4, False) is True, "目标更早 → 往前翻必须成功"
    assert [m for (_y, m) in p4.panels] == [9, 10], p4.panels
    assert ad4._nav_month(p4, True) is True, "目标更晚 → 往后翻必须成功"
    assert [m for (_y, m) in p4.panels] == [10, 11], p4.panels

    print("✅ 翻月方向(成对协议: 目标月不可见 → min/max 判方向)通过")

    print("✅ 全部通过 (美团日期控件修复·旧用例)")






# ---------------------------------------------------------------- v2: 成对协议(现场复刻 + 对抗)
def test_pair_clean_state_scene():
    """① 现场复刻·干净态: [九月(含10月尾巴)][十月], 初始 ["",""], 目标 2026-09-08 → [09-08, 09-08]。

    钉死 2026-10-14 现场(diag_download_20261014-150407): 要 09-08 却设成 **10-08** ——
    "每击即验证 + 不对就换候选"的第一击(正确格)读回没变 → 误判失败 → 换到尾巴格 → 凑出 [09-08, 10-08]。
    契约: **所有格点击的真实日期必须全是 2026-09-08**(绝无 10-08), 且序列 = [格①, 结束框, 格②]
    (格① == 格② 同一格)。
    """
    page = CalPage(_PANELS_9_10, values=["", ""])
    ad, logs = _mk(page)
    ad._set_date_range(page, "2026-09-08", "2026-09-08")
    assert page.inputs[0]["v"] == "2026-09-08" and page.inputs[1]["v"] == "2026-09-08", page.inputs
    assert all(c["date"] == "2026-09-08" and not c["tail"] for c in page.cell_clicks), page.cell_clicks
    assert page.cross_month_pairs == [], f"绝不许凑出跨月对: {page.cross_month_pairs}"
    want = _real_center(2026, 9, 8, _PANELS_9_10)
    assert (page.cell_clicks[0]["x"], page.cell_clicks[0]["y"]) == want, page.cell_clicks[0]
    # 序列形状: [格①, 结束框, 格②] —— 两击必须落在**同一个格**
    assert page.events == [("cell", "2026-09-08", (2026, 9)),
                           ("input", 1),
                           ("cell", "2026-09-08", (2026, 9))], page.events
    assert "🧹 日期" not in "".join(m for _l, m in logs), f"一次就成, 不该清场: {logs}"
    rows = _logs(logs)
    assert len(rows) == 1 and "×2" in rows[0] and "→ 读回 ['2026-09-08', '2026-09-08']" in rows[0], rows
    assert any("📅 日期区间 = 2026-09-08 ~ 2026-09-08" in m for _l, m in logs), logs
    # 反向确认: 假件确实复现真机陷阱 —— "8" 在双面板里重复出现(含别月格 10/8、11/8)
    dup = [c for c in page.cells() if c["d"] == "8"]
    assert len(dup) >= 4, dup
    others = [c for c in dup if c["_date"].isoformat() != "2026-09-08"]
    assert others and all(c["_date"].isoformat() in ("2026-10-08", "2026-11-08") for c in others), others

    # 真机开局 = 面板**关着**(要 ① 点开始框): 序列形状必须是 [开始框, 格①, 结束框, 格②]
    page2 = CalPage(_PANELS_9_10, values=["", ""], picker_open=False)
    ad2, logs2 = _mk(page2)
    ad2._set_date_range(page2, "2026-09-08", "2026-09-08")
    assert page2.events == [("input", 0),
                            ("cell", "2026-09-08", (2026, 9)),
                            ("input", 1),
                            ("cell", "2026-09-08", (2026, 9))], page2.events
    assert page2.inputs[0]["v"] == "2026-09-08" and page2.inputs[1]["v"] == "2026-09-08", page2.inputs
    print("✅ ① 现场复刻·干净态: 所有格击都是 9/8, 序列=[格①, 结束框, 格②](同一格), 读回整对一致")


def test_pair_dirty_state_repair():
    """② 现场复刻·脏态修复: 初始就是今天的终态 ["2026-09-08","2026-10-08"] → 必须变成 [09-08, 09-08]。

    脏态(待定/已选起点)下**同一格两击会覆盖重建区间** —— 写成测试钉死。
    """
    page = CalPage(_PANELS_9_10, values=["2026-09-08", "2026-10-08"])
    ad, logs = _mk(page)
    ad._set_date_range(page, "2026-09-08", "2026-09-08")
    assert page.inputs[0]["v"] == "2026-09-08" and page.inputs[1]["v"] == "2026-09-08", page.inputs
    assert all(c["date"] == "2026-09-08" and not c["tail"] for c in page.cell_clicks), page.cell_clicks
    assert page.cross_month_pairs == [], f"脏态修复也不许凑出跨月对: {page.cross_month_pairs}"
    assert "".join(m for _l, m in logs).count("🖱 日期尝试") >= 1, logs
    print("✅ ② 现场复刻·脏态修复: [09-08,10-08] → 同格两击重建 → [09-08,09-08]")


def test_pair_adversarial_first_candidate_inert():
    """③ 对抗·首候选两击不成: rank#1 格"点了只置待定"(模拟该格不生效)。

    契约: 协议必须(换候选或清场重来)**从不产生**跨月对 [09-08,10-08]; 最终要么 [09-08,09-08],
    要么 False(经 `_set_date_range` 走 gate 抛**逐字**原文案)。两种结局都算通过。
    """
    page = CalPage(_PANELS_9_10, values=["", ""], pending_only_dates=["2026-09-08"])
    ad, logs = _mk(page)
    try:
        state = ("ok", ad._set_date_range(page, "2026-09-08", "2026-09-08"))
    except RuntimeError as e:
        state = ("raise", str(e))
    assert page.cross_month_pairs == [], f"绝不许凑出跨月对: {page.cross_month_pairs}"
    if state[0] == "ok":
        assert page.inputs[0]["v"] == "2026-09-08" and page.inputs[1]["v"] == "2026-09-08", page.inputs
        assert any("📅 日期区间 = 2026-09-08 ~ 2026-09-08" in m for _l, m in logs), logs
        note = "成功(换候选/清场重来后拿到正确对)"
    else:
        # 走 gate: **逐字**原文案(只换日期值); 且最终绝不留着想要的区间假装成功
        assert state[1] == ("日期区间设置未生效: 期望 2026-09-08~2026-09-08, "
                            "实际 [%r, %r] (已中止, 不用错误区间下载)"
                            % (page.inputs[0]["v"], page.inputs[1]["v"])), state[1]
        assert page.inputs[0]["v"] != "2026-09-08" or page.inputs[1]["v"] != "2026-09-08", page.inputs
        assert any("🧹 日期: 清场重来" in m for _l, m in logs), f"失败前必须清场重来过: {logs}"
        note = "失败但与现场一致(干净地报错, 不带错区间下载)"
    # 无论成败, 假件里"完成区间"写出来的**整对**必须都是同月对(真机现场那个跨月对已被排除)
    assert all(a[:7] == b[:7] for a, b in page.pair_reads), page.pair_reads
    print(f"✅ ③ 对抗·首候选两击不成 → 无跨月对(只出同月对), 结局={note}")


def test_pair_single_click_variant():
    """④ 单击完成变体(single_click_completes=True): 一击后即成, 成功且**没有第二击**。"""
    page = CalPage(_PANELS_9_10, values=["", ""], single_click_completes=True)
    ad, logs = _mk(page)
    ad._set_date_range(page, "2026-09-08", "2026-09-08")
    assert page.inputs[0]["v"] == "2026-09-08" and page.inputs[1]["v"] == "2026-09-08", page.inputs
    assert len(page.cell_clicks) == 1, f"单击即成的组件只该点 1 下: {page.cell_clicks}"
    assert page.cell_clicks[0]["date"] == "2026-09-08" and not page.cell_clicks[0]["tail"], page.cell_clicks
    assert any("📅 日期区间 = 2026-09-08 ~ 2026-09-08" in m for _l, m in logs), logs
    print("✅ ④ 单击完成变体: 一击即成, 没有第二击")


def test_pair_close_after_complete_variant():
    """⑤ 完成后关闭变体(close_after_complete=True): 完成即关面板 → 仍须成功。"""
    page = CalPage(_PANELS_9_10, values=["", ""], close_after_complete=True)
    ad, logs = _mk(page)
    ad._set_date_range(page, "2026-09-08", "2026-09-08")
    assert page.inputs[0]["v"] == "2026-09-08" and page.inputs[1]["v"] == "2026-09-08", page.inputs
    assert page.cross_month_pairs == [], page.cross_month_pairs
    assert any("📅 日期区间 = 2026-09-08 ~ 2026-09-08" in m for _l, m in logs), logs
    print("✅ ⑤ 完成后关闭变体: 仍成功(整对读回在面板关闭后依然可读)")


def test_pair_first_click_closes_panel():
    """⑤b 第 1 击就把面板点收起(close_after_first_cell 类行为) → 必须重新打开面板再继续, 不许空转到 False。"""
    page = CalPage(_PANELS_9_10, values=["", ""], first_cell_closes=True)
    ad, logs = _mk(page)
    ad._set_date_range(page, "2026-09-08", "2026-09-08")
    assert page.inputs[0]["v"] == "2026-09-08" and page.inputs[1]["v"] == "2026-09-08", page.inputs
    assert page.input_clicks, "面板被收起后必须重新点开输入框"
    assert page.cross_month_pairs == [], page.cross_month_pairs
    print("✅ ⑤b 面板被点收起 → 重新打开 → 成对完成")


def test_pair_wall_clock_and_cleanup():
    """⑥ 上限纪律 + 清场 + 总失败路径: 面板翻不动(flip_works=False)时目标月永远不可见 →
    有限次点击、有限时间后走 gate; 断言**先调用 `_type_date`**(键盘兜底)、**再抛逐字原文案**;
    且不许在面板没到位时乱点格。
    """
    import time as _t
    page = CalPage([(2026, 10, 100, 200), (2026, 11, 460, 200)], values=["", ""], flip_works=False)
    ad, logs = _mk(page)
    typed = []

    def fake_type(p, df, dt):
        typed.append((df, dt))                                # 键盘兜底也无效(现场如此): 什么都不写

    ad._type_date = fake_type
    t0 = _t.time()
    try:
        ad._set_date_range(page, "2026-09-08", "2026-09-08")
        raise AssertionError("必须抛错(不带错区间下载)")
    except RuntimeError as e:
        assert str(e) == ("日期区间设置未生效: 期望 2026-09-08~2026-09-08, "
                          "实际 ['', ''] (已中止, 不用错误区间下载)"), str(e)
    assert typed == [("2026-09-08", "2026-09-08")], f"必须先走键盘兜底: {typed}"
    assert _t.time() - t0 < 5, "翻月无效时必须快速失败(不许长时间试探)"
    assert page.cell_clicks == [], f"目标月没到位时不许点格: {page.cell_clicks}"
    assert any("⚠️ 日历网格点选未成功, 改键盘输入日期" in m for _l, m in logs), logs
    assert any("🧹 日期: 清场重来" in m for _l, m in logs), f"失败前必须清场重来过: {logs}"
    print("✅ ⑥ 上限纪律: 翻月无效 → 快速失败 → 键盘兜底 + 清场日志 + 逐字 gate 抛错")


def test_pair_dead_chevron_fallback():
    """⑧b 同方向第一个 chevron 是死的(点后月份没变) → 换同方向另一个候选; 都不行 → False。

    与 _nav_month 的"点后读回面板月份"契约同一件事: 死按钮不许被当成"翻到位"。
    """
    page = CalPage([(2026, 10, 100, 200), (2026, 11, 460, 200)], values=["", ""],
                   dead_chevron=True)
    ad, logs = _mk(page)
    ad._set_date_range(page, "2026-09-08", "2026-09-08")
    assert any(not c["ok"] for c in page.chevron_clicks), "应有一次无效的 chevron 点击"
    assert any(c["ok"] for c in page.chevron_clicks), "换到另一个 chevron 后必须成功"
    assert page.cell_clicks and all(c["date"] == "2026-09-08" and c["panel"] == (2026, 9)
                                    for c in page.cell_clicks), page.cell_clicks
    assert page.cross_month_pairs == [], page.cross_month_pairs
    print("✅ ⑧b 死 chevron → 换同向另一个候选 → 翻到位后成对成功")


def test_candidate_ranking():
    """⑦ 候选排序(软化): 属性精确匹配最优先; 明确是别的日期的属性靠后; 尾巴格降权。

    排序只是优先序, 正确性由**整对读回**兜底(成对协议里不再有"每击验证")。
    """
    page = CalPage(_PANELS_9_10, values=["", ""])
    ad, _ = _mk(page)
    cands = ad._day_candidates(page, 2026, 9, 8)
    assert cands, "九月面板里应有 day=8 的候选"
    assert all(not c["other"] for c in cands[:1]), cands[:1]
    assert all(c["cx"] < 460 for c in cands), "候选格必须限定在九月面板 bbox 内"
    page2 = CalPage([(2026, 9, 100, 200)], values=["", ""], attrs=False)   # 无属性版本
    cands2 = ad._day_candidates(page2, 2026, 9, 8)
    assert cands2 and not cands2[0]["other"], f"尾巴格不许排最前: {cands2[:1]}"
    print("✅ ⑦ 候选排序: bbox 限定 + 尾巴降权 + 属性优先(仅排序, 不替代整对验证)")


def test_pair_nav_month_cross_month_target():
    """⑧ 翻月后再成对: 目标 2026-09 不在可见面板([十月][十一月]) → 先按 min/max 判方向往前翻,
    翻到位后仍必须走成对序列; 且**只许往前翻**。
    """
    page = CalPage([(2026, 10, 100, 200), (2026, 11, 460, 200)], values=["2026-11-01", "2026-11-01"])
    ad, logs = _mk(page)
    ad._set_date_range(page, "2026-09-08", "2026-09-08")
    assert page.chevron_clicks and all(not c["fwd"] for c in page.chevron_clicks), page.chevron_clicks
    hits = page.cell_clicks
    assert hits and all(c["date"] == "2026-09-08" and c["panel"] == (2026, 9) for c in hits), hits
    assert hits[0]["date"] == hits[-1]["date"], "两击必须同一格"
    print("✅ ⑧ 翻月 + 成对: 只往前翻, 两击都落在九月面板的 9/8")


def test_pair_generic_range_and_click_budget():
    """⑨ 通用区间(df!=dt) + 点击预算: 第 2 击点 dt 的候选 → [09-08, 09-10];
    并核对"总点击序列 ≤9 次"(3 大轮 × 3 候选 × 2 击的硬上限) 与"每对两击同格/同面板"的不变量。
    """
    page = CalPage(_PANELS_9_10, values=["", ""])
    ad, logs = _mk(page)
    ad._set_date_range(page, "2026-09-08", "2026-09-10")
    assert page.inputs[0]["v"] == "2026-09-08" and page.inputs[1]["v"] == "2026-09-10", page.inputs
    assert page.cross_month_pairs == [], page.cross_month_pairs
    assert [c["date"] for c in page.cell_clicks] == ["2026-09-08", "2026-09-10"], page.cell_clicks
    assert all(c["panel"] == (2026, 9) and not c["tail"] for c in page.cell_clicks), page.cell_clicks
    assert len(page.cell_clicks) <= 9, f"点击预算: {page.cell_clicks}"

    # 干净态一次就成: 2 击 + 1 次结束框, 不许多余试探
    page2 = CalPage(_PANELS_9_10, values=["", ""])
    ad2, logs2 = _mk(page2)
    ad2._set_date_range(page2, "2026-09-08", "2026-09-08")
    assert page2.events == [("cell", "2026-09-08", (2026, 9)), ("input", 1),
                            ("cell", "2026-09-08", (2026, 9))], page2.events
    assert "".join(m for _l, m in logs2).count("🖱 日期尝试") == 1, logs2
    # 已是目标值 → 直接放行, 一次点击都不发(幂等, 不折腾)
    page3 = CalPage(_PANELS_9_10, values=["2026-09-08", "2026-09-08"])
    ad3, logs3 = _mk(page3)
    ad3._set_date_range(page3, "2026-09-08", "2026-09-08")
    assert page3.events == [] and page3.cell_clicks == [], page3.events
    print("✅ ⑨ 通用区间(09-08~09-10) + 点击预算/幂等: 只用必要的点击")


def main_all():
    main()
    test_pair_clean_state_scene()
    test_pair_dirty_state_repair()
    test_pair_adversarial_first_candidate_inert()
    test_pair_single_click_variant()
    test_pair_close_after_complete_variant()
    test_pair_first_click_closes_panel()
    test_pair_wall_clock_and_cleanup()
    test_candidate_ranking()
    test_pair_nav_month_cross_month_target()
    test_pair_dead_chevron_fallback()
    test_pair_generic_range_and_click_budget()
    print("✅ 全部通过 (任务H: 美团日期成对协议 —— 同一对格子两击 + 整对读回 + Esc 清场重来)")


if __name__ == "__main__":
    main_all()
