# -*- coding: utf-8 -*-
"""流量页(商圈采集)日期: 必须拿到硬证据才算生效; 未确认则**放弃该店**(留空)。

真机背景(2026-10-02 用户报"美团外卖没有根据报表区间的时间来处理, 默认选的昨日"):
  日志 `📅 流量页日期 → 2026-09-30（未确认）` 之后照抓 → 商圈TOP 列写的是默认日期的数,
  数字看着正常但日期是错的(最危险的错法)。

2026-10-08 真机破案(用户报"商圈TOP还是没拿到"): 设置侧**两个** bug 让日期根本没设进去 ——
  ① 「自定义」innerText 实为 `自定义\n~` → `=== '自定义'` 精确匹配永远点不到;
  ② roo 的 range 日期选择器不吃 input.value/键入 → 必须在其面板里**点两次同一格**(起点+终点)。

2026-10-10 跨月修复(用户 09-29 回填: 18/18 店全挂「日期未能确认」): 旧翻月代码用
  `document.querySelector('.roo-datepicker-data-panel...')` 抓到的是**隐藏年图层**
  (它的头是「2021-2032」、翻页=12年年跳) → 跨月目标从未成功过。改法: 可见月标题
  (「2026十月」, 中文数字月) + **可见面板里的单月 chevron-left/right** 逐月逼近,
  且明确排除 double-left/right(年跳); 另修: 打开面板偶发首点不生效 → 以可见头为准重试点击。

本文件锁: ① 验收必须有硬证据(输入框 / 「已选时间」) ② 未确认必须留取证 ③ 点两次的机制不许退化
  ④ 跨月不许再回退到"隐藏年图层"(隐藏头解析必须被拒) ⑤ 月翻页只许用单月 chevron。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan as mt                           # noqa: E402
from merchant_report.platforms.meituan import MeituanAdapter                   # noqa: E402

mt.time.sleep = lambda *_: None          # 瞬时化(本文件全程假件驱动; 真机节奏由探针负责)


class FakeInputs:
    """够用的 input 集合假件: 新实现只用 nth()/input_value()/click()。"""

    def __init__(self, page, values):
        self.page, self.values, self._i = page, values, 0

    def count(self):
        return len(self.values)

    @property
    def first(self):
        self._i = 0
        return self

    def nth(self, i):
        self._i = i
        return self

    def click(self, timeout=None):
        return None

    def fill(self, v):
        self.values[self._i] = ""

    def type(self, v, delay=None):
        return None

    def input_value(self):
        return self.values[self._i]


class FakeLocator(FakeInputs):
    """打标记后的 locator: 被点一次 = 面板里点了一格(由页面假件计数)。"""

    def click(self, timeout=None):
        self.page.clicks += 1
        return None


class FakeMouse:
    def __init__(self, page):
        self.page = page

    def click(self, x, y=None):
        self.page.mouse_clicks += 1
        if y is None:
            return None
        # 点日期输入框 → 记聚焦(键入路径要读它); 点 (10,10)=翻月 由 evaluate 假件计数
        for i, inp in enumerate(self.page.flow_inputs()):
            if abs(inp["x"] - x) < 3 and abs(inp["y"] - y) < 3:
                self.page.focused = i
                return None
        return None


class FakeKeyboard:
    """键入路径的假件: type 把 ASCII 逐字符交给页面(受控组件会吞掉 → type_sticks)。"""

    def __init__(self, page):
        self.page = page

    def press(self, key):
        p = self.page
        if key == "ControlOrMeta+a":
            p.typed = []                          # 全选 → 清空缓冲
            return
        if key == "Tab":
            p.focused = None
            return
        if key == "Escape":
            p.typed = []
            return
        if key == "Enter":
            if p.type_sticks and p.focused is not None:
                buf = "".join(p.typed)
                if len(buf) == 10:
                    p.values[p.focused] = buf      # 页面吃下键入 → 写进输入框
            p.typed = []
            return

    def type(self, text, delay=None):
        self.page.typed.extend(list(text))


class FakePage:
    """accepts=False 模拟"页面没吃下我们的日期选择"(还停在默认昨日)。

    - clicks 计数模拟"点两次日格子"(点满 2 次且 accepts=True 时页面才把日期写进输入框);
    - months = 可见月标题列表(可动态改), 用于驱动**跨月翻页**测试: 点单月 chevron 时翻一页;
    - 2026-10-15 任务 J 新增两个开关, 用于建模"键入不生效 → 日历兜底"两态:
        inputs=None      不给日期输入框(页面结构里压根没有 placeholder 开始/结束时间);
        type_sticks=False 键入不吃(React 受控; Ctrl+A+type 后输入框仍空) —— 真机 10-14 就是这个态;
                          True = 这个页面吃键入(直接键入即生效, 不该再进日历)。
    """

    def __init__(self, accepts=True, values=None, body="", day_cells=1, months=None,
                 inputs=True, type_sticks=False):
        self.accepts = accepts
        self.values = values if values is not None else ["2026-09-29", "2026-09-29"]
        self.body = body
        self.url = "https://waimaieapp.meituan.com/flow"
        self.clicks = 0
        self.day_cells = day_cells
        self.months = months if months is not None else ["2026九月", "2026十月"]
        self.nav_clicks = 0
        self.mouse_clicks = 0
        self.inputs = inputs
        self.type_sticks = type_sticks
        self.focused = None
        self.typed = []                  # 键入序列(ASCII 字符按 30ms 一个到达, 这里按帧收集)
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard(self)

    def flow_inputs(self):
        """流量页日期输入框快照(与 meituan._FLOW_INPUTS_JS 同形: tag/placeholder/值)。"""
        if not self.inputs:
            return []
        return [{"x": 100.0, "y": 60.0, "cx": 100.0, "cy": 60.0, "v": self.values[0],
                 "ph": "开始时间", "tag": "start", "cls": "roo-input", "ro": False, "dis": False},
                {"x": 300.0, "y": 60.0, "cx": 300.0, "cy": 60.0, "v": self.values[1],
                 "ph": "结束时间", "tag": "end", "cls": "roo-input", "ro": False, "dis": False}]

    def evaluate(self, js, arg=None):
        if "roo-datepicker-header" in js:            # 可见月标题(隐藏头不在假件里)
            return list(self.months)
        if "chevron" in js and "roo-icon" in js:     # 单月翻页按钮定位
            self.nav_clicks += 1
            # 模拟"点击后翻一个月": 十月 → 九月(足够覆盖本测的跨月目标)
            if self.months and "十月" in self.months[0]:
                self.months = ["2026九月"]
            return {"x": 10, "y": 10}
        if "document.body" in js and "innerText" in js:   # 读正文(容忍空格差异)
            return self.body
        if "pri = p =>" in js:                       # _FLOW_INPUTS_JS(日期控件探测)
            return self.flow_inputs()
        if "data-hm-day" in js:                      # 面板里打日格子标记
            return self.day_cells
        if "data-hm-custom" in js:                   # 「自定义」标记
            return 1
        return 0

    def locator(self, sel):
        if "data-hm-day" in sel:                     # 点日格子 → 计数
            return FakeLocator(self, self.values)
        return FakeInputs(self, self.values)

    def tick(self, date):
        """由测试驱动: 模拟"点满了两次"后页面把日期填进输入框。"""
        if self.accepts and self.clicks >= 2:
            self.values[0] = self.values[1] = date
            self.body = f"已选时间：{date} 自定义"


def mk(page):
    ad = mt.MeituanAdapter.__new__(mt.MeituanAdapter)
    logs = []
    ad.page = page
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append((level, m))})()
    return ad, logs


def _relax_page(**kw):
    """严格路径(面板候选 + td 标记)全空的假页; 只在**宽松找格** JS 上给出坐标。

    真机 2026-09 背景(任务 M2): 流量页 18 店×2 轮全败, desc `... (未找到)` = `_day_candidates`
    空 **且** `_MARK_DAY_JS` 标记 0 个 —— 第 1 击就找不到格子。宽松兜底是**放弃前**的最后手段。
    """
    p = FakePage(**kw)
    relax = [{"x": 150.0, "y": 210.0, "cx": 135.0, "cy": 198.0, "w": 30.0, "h": 24.0,
              "cls": "day-cell", "anc": " roo-datepicker-body", "chain": "td.day-cell"}]

    def ev(js, arg=None):
        if "out.length >= 5" in (js or ""):          # _RELAX_DAY_JS 的独有标记
            return list(relax)
        if "data-hm-day" in (js or ""):
            return 0                                 # 严格标记路径 0 个
        return FakePage.evaluate(p, js, arg)

    p.evaluate = ev
    return p


def _count_all_clicks(p):
    """把**每一次**鼠标点击都当"点到格子上"计数(配合 accepts=True 的页面模型)。"""
    def _c(x, y=None):
        p.mouse_clicks += 1
        p.clicks += 1
        p.tick("2026-09-30")
    p.mouse.click = _c


def test_m2_second_click_ladder():
    """任务 M2: 第 2 击阶梯加固 —— 重解析瞬态(第 1 次空/第 2 次正常)必须**重试后成功**, desc 带 ×2。

    真机证据(2026-10-15 ndjson): 对账页 desc `2026-09面13`(无 ×2 / 无(未找到)) = 第 2 击前
    重新解析拿到空 → 旧代码直接 `return False`, 而**三条失败路径 desc 完全一样**, 远程分不清。
    """
    p = FakePage(accepts=True, inputs=False)          # 无日期输入框 → 跳过键入, 直落日历
    _count_all_clicks(p)
    ad, logs = mk(p)
    cell = {"x": 150.0, "y": 210.0, "cx": 135.0, "cy": 198.0, "w": 30.0, "h": 24.0,
            "d": "30", "cls": "", "anc": "", "attr": "2026-09-30", "dis": False,
            "other": False, "_panel": (2026, 9), "_tail": False}
    seq = [list([cell]), [], list([cell])]            # 第 1 次重解析空 → 第 2 次正常

    def _cands(page, y, m, d):
        return list(seq.pop(0)) if seq else list([cell])
    ad._day_candidates = _cands
    assert ad._set_flow_date(p, "2026-09-30") is True, logs
    assert any("×2" in m and "已生效" in m for _, m in logs), f"成功 desc 必须带 ×2: {logs}"
    assert p.clicks >= 2, "同一格必须点满两次"
    print("✅ M2 第2击瞬态(重解析第1次空/第2次正常) → 重试后成功, desc 带 ×2")

    # 三条失败路径必须**可区分**(真机远程排错靠它)
    pair = inspect.getsource(MeituanAdapter._click_same_cell_twice)
    pair_all = pair + inspect.getsource(MeituanAdapter._second_click_relax)
    for tag in ("第1击未击发", "第2击重解析空", "第2击未击发"):
        assert tag in pair_all, f"失败 desc 必须能区分路径: 缺 {tag}"
    assert "_pick_anchored" in pair, "第 2 击必须走锚点选格(不许盲取 cands[0])"
    assert "range(3)" in pair and "_pair_last_fail" in pair, "要有重解析重试 + 失败细节实例属性"
    print("✅ M2 契约: 三条失败路径可区分 + 锚点选格 + 重解析重试 + _pair_last_fail")


def test_m2_relaxed_fallback():
    """任务 M2: 严格路径全空 → **宽松找格**兜底给坐标 → 成功(生效判定仍只认读回硬证据)。"""
    p = _relax_page(accepts=True, inputs=False)
    _count_all_clicks(p)
    ad, logs = mk(p)
    ad._day_candidates = lambda page, y, m, d: []      # 严格候选永远空
    assert ad._set_flow_date(p, "2026-09-30") is True, logs
    assert any("宽松" in m and "×2" in m for _, m in logs), f"desc 要标注宽松兜底: {logs}"
    assert any("已生效" in m for _, m in logs), logs
    print("✅ M2 宽松兜底: 严格全空 → 宽松坐标 → 两击 → 读回通过(放宽的只是找格手段)")

    # 宽松也不中(页面不吃) → 未确认 + 失败 desc 注明「宽松亦未找到」, 绝不用默认日期冒充
    p2 = _relax_page(accepts=False, inputs=False, values=["2026-09-29", "2026-09-29"])
    _count_all_clicks(p2)
    ad2, logs2 = mk(p2)
    ad2._day_candidates = lambda page, y, m, d: []
    assert ad2._set_flow_date(p2, "2026-09-30") is False, logs2
    assert any("未确认" in m for _, m in logs2), logs2
    src = inspect.getsource(MeituanAdapter._find_day_cell_relaxed)
    assert "innerText" in MeituanAdapter._RELAX_DAY_JS and "5 <" not in MeituanAdapter._RELAX_DAY_JS, \
        "宽松找格: innerText 精确相等 + 尺寸 5<w<64"
    assert "old|other|prev|next|disabl" in MeituanAdapter._RELAX_DAY_JS, "宽松找格要跳邻月/禁用格"
    assert "out.length >= 5" in MeituanAdapter._RELAX_DAY_JS, "宽松候选要有上限(~5)"
    mark = inspect.getsource(MeituanAdapter._mark_day_cell)
    assert "宽松亦未找到" in mark, "宽松兜底也失败时 desc 必须注明"
    assert "_relax_day_forensics" in mark or "_relax_day_forensics" in \
        inspect.getsource(MeituanAdapter._set_flow_date) or "_relax_day_forensics" in \
        inspect.getsource(MeituanAdapter._set_bill_inputs), "现场包要含宽松几何取证"
    print("✅ M2 契约: 宽松兜底(innerText/尺寸/跳邻月/上限5) + 失败注明 + 几何取证")


class PackPage(FakePage):
    """带 screenshot/frames/url 的假页(真 collect_diagnostic_pack 集成用例用)。"""

    def screenshot(self, path=None, timeout=None):
        raise RuntimeError("假件没有截图能力")

    @property
    def frames(self):
        return []


def test_m2_date_pack():
    """任务 M2: 失败现场包 —— 每 phase 每轮**只写一次** + sink 出现「日期现场」日志 + 取证异常不崩。"""
    import tempfile
    import merchant_report.diagnose as diag

    # ① monkeypatch: 断言只调一次 + extra 有 phase/date/readback + 🆘 日志
    calls = []
    orig = diag.collect_diagnostic_pack
    diag.collect_diagnostic_pack = lambda page, step, err, ddir=None, extra=None: (
        calls.append({"page": page, "step": step, "err": err, "ddir": ddir, "extra": extra}),
        "/tmp/fake-diag.json")[1]
    try:
        p = FakePage(accepts=False, inputs=False, values=["", ""])
        _count_all_clicks(p)
        ad, logs = mk(p)
        assert ad._set_flow_date(p, "2026-09-30") is False, logs
        assert len(calls) == 1, f"每 phase 每轮只许写一次现场包: {len(calls)}"
        assert calls[0]["step"] == "date-flow", calls[0]
        extra = calls[0]["extra"]
        assert extra["phase"] == "flow" and extra["date"] == "2026-09-30", extra
        assert "readback" in extra and "candidates_n" in extra and "heads" in extra, extra
        assert any("日期现场" in m for _, m in logs), logs
        # 第二次(同一实例, 同一 phase) → 只记路径不重写
        assert ad._date_pack(p, "flow", "2026-09-30", {"x": 1}) == ""
        assert len(calls) == 1, "同 phase 第二次不许重写(避免刷文件)"
        # bill phase 独立一次
        ad._date_pack(p, "bill", "2026-09-30", {"x": 1})
        assert len(calls) == 2 and calls[1]["step"] == "date-bill", calls

        # ② 取证自身抛异常 → 主流程照常返回 False, 不崩、不记 🆘
        diag.collect_diagnostic_pack = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        p2 = FakePage(accepts=False, inputs=False, values=["", ""])
        _count_all_clicks(p2)
        ad2, logs2 = mk(p2)
        assert ad2._set_flow_date(p2, "2026-09-30") is False, logs2
        assert not any("日期现场" in m for _, m in logs2), logs2
        print("✅ M2 现场包: 每 phase 一次 + extra 字段齐 + 🆘 日志; 取证抛异常 → 主流程照常 False")
    finally:
        diag.collect_diagnostic_pack = orig

    # ③ 真 collect_diagnostic_pack + 缺截图能力的假页 → 静默跳过, 落盘到 download_dir, 不抛
    d = tempfile.mkdtemp(prefix="mtm2-")
    p3 = PackPage(accepts=False, inputs=False, values=["", ""])
    _count_all_clicks(p3)
    ad3, logs3 = mk(p3)
    ad3.download_dir = d
    assert ad3._set_flow_date(p3, "2026-09-30") is False, logs3
    import os
    files = sorted(os.listdir(d))
    assert any(f.startswith("diag_date-flow") and f.endswith(".json") for f in files), files
    assert any("日期现场" in m for _, m in logs3), logs3
    print(f"✅ M2 现场包落盘(缺 screenshot 也不抛): {files}")


def main():
    # ① 面板点满两次 → 输入框拿到目标日期 → 判「已生效」
    p = FakePage(accepts=True)
    ad, logs = mk(p)

    class AutoTick(FakeLocator):
        def click(self, timeout=None):
            self.page.clicks += 1
            self.page.tick("2026-09-30")            # 每次点完就让"页面"按当前点击数更新
            return None

    p.locator = lambda sel: AutoTick(p, p.values) if "data-hm-day" in sel else FakeInputs(p, p.values)
    assert ad._set_flow_date(p, "2026-09-30") is True, logs
    assert p.values == ["2026-09-30", "2026-09-30"], p.values
    assert any("已生效" in m for _, m in logs), logs
    print("✅ 点两次日格子 → 输入框拿到目标日期 → 判「已生效」")

    # ② 页面没吃下(点多少次都不写) → 未确认, 且必须留下取证
    p2 = FakePage(accepts=False, values=["2026-09-29", "2026-09-29"], body="今日 昨日 自定义 已选时间：2026-09-29")
    ad2, logs2 = mk(p2)
    assert ad2._set_flow_date(p2, "2026-09-30") is False, logs2
    assert any("未确认" in m for _, m in logs2), logs2
    assert any("取证" in m for _, m in logs2), logs2
    print("✅ 页面没吃下选择(仍是昨日) → 判「未确认」+ 留下取证")
    # ③ 页面文本里有硬标记(输入框读不到时的第二证据)
    p3 = FakePage(accepts=False, values=["", ""], body="已选时间：2026/09/30 自定义")
    ad3, logs3 = mk(p3)
    assert ad3._set_flow_date(p3, "2026-09-30") is True, logs3
    print("✅ 页面标记「已选时间：2026/09/30」(斜杠写法) → 也算生效")

    # ④ 跨月(2026-10-10 修复): 初始停在「2026十月」→ 必须靠单月 chevron 翻到九月再点格子
    p4 = FakePage(accepts=True, months=["2026十月"])
    ad4, logs4 = mk(p4)

    class AutoTick4(FakeLocator):
        def click(self, timeout=None):
            self.page.clicks += 1
            self.page.tick("2026-09-30")
            return None

    p4.locator = lambda sel: AutoTick4(p4, p4.values) if "data-hm-day" in sel else FakeInputs(p4, p4.values)
    assert ad4._set_flow_date(p4, "2026-09-30") is True, logs4
    assert p4.nav_clicks >= 1, f"跨月目标必须触发翻月(nav_clicks={p4.nav_clicks})"
    assert p4.mouse_clicks >= 1, "翻月必须用真鼠标点击(合成 click 不稳)"
    assert p4.values == ["2026-09-30", "2026-09-30"], p4.values
    print(f"✅ 跨月: 2026十月 →(单月chevron)→ 2026九月 → 点两次格子 → 生效")

    # ⑥ 2026-10-15 任务 M4: 本页**键入已停用**(探针实测读回恒 ['','']) → 绝不再键入;
    #    窗口内的日子一律走日历。这里把 `_flow_type_dates` 换成"调用即炸"来钉死这点。
    p5 = FakePage(accepts=True, values=["", ""], type_sticks=True)
    ad5, logs5 = mk(p5)
    _boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("_flow_type_dates 不得再被调用"))
    ad5._flow_type_dates = _boom

    class AutoTick5(FakeLocator):
        def click(self, timeout=None):
            self.page.clicks += 1
            self.page.tick("2026-09-30")
            return None

    p5.locator = lambda sel: AutoTick5(p5, p5.values) if "data-hm-day" in sel else FakeInputs(p5, p5.values)
    assert ad5._set_flow_date(p5, "2026-09-30") is True, logs5
    assert p5.values == ["2026-09-30", "2026-09-30"], p5.values
    assert p5.typed == [], f"任务 M4: 流量页不得再键入任何字符: {p5.typed}"
    assert not any("键入" in m for _, m in logs5), f"不该再有键入相关日志: {logs5}"
    print("✅ 任务M4: 流量页键入已停用(不键入、无键入日志) → 窗口内直接走日历")

    # ⑦ 2026-10-15 任务 J/M4: 窗口内 → 自动落日历点选(硬证据 = 输入框读回 == 目标日)
    p6 = FakePage(accepts=True, values=["", ""], type_sticks=False)
    ad6, logs6 = mk(p6)

    class AutoTick6(FakeLocator):
        def click(self, timeout=None):
            self.page.clicks += 1
            self.page.tick("2026-09-30")
            return None

    p6.locator = lambda sel: AutoTick6(p6, p6.values) if "data-hm-day" in sel else FakeInputs(p6, p6.values)
    assert ad6._set_flow_date(p6, "2026-09-30") is True, logs6
    assert any("日历点选" in m and "已生效" in m for _, m in logs6), logs6
    assert p6.values == ["2026-09-30", "2026-09-30"], p6.values
    print("✅ 窗口内 → 日历点选(同一格两击)生效(真实机 10-15 那一态)")

    # ⑦' 日历也不成 → 未确认(留空), 绝不用默认日期的数据冒充
    p7 = FakePage(accepts=False, values=["", ""], type_sticks=False)
    ad7, logs7 = mk(p7)
    assert ad7._set_flow_date(p7, "2026-09-30") is False, logs7
    assert any("未确认" in m for _, m in logs7), logs7
    print("✅ 日历不成 → 「未确认」(调用方留空)")

    # ⑧ 关键: 调用方(capture_circle)必须在未确认时放弃该店, 不能继续抓
    src = inspect.getsource(mt.MeituanAdapter.capture_circle)
    assert "if not self._set_flow_date(page, date)" in src, "capture_circle 必须检查日期是否确认"
    assert "该店商圈列留空" in src, "未确认时应放弃该店(留空)而不是照抓"
    print("✅ capture_circle: 日期未确认 → 放弃该店(商圈列留空), 不拿默认日期冒充")

    print("✅ 流量页日期(键入/日历两态 + 硬证据验收 + 未确认即放弃 + 跨月翻页)单测全部通过")


def test_click_twice_contract():
    """锁住 2026-10-08 / 2026-10-10 / 2026-10-15 的真根因, 防止退化。

    真机证据: 点1次后 已选时间仍是 2026-10-07 / 输入框 ['',''];
              点2次后 已选时间 2026-10-06 / 输入框 ['2026-10-06','2026-10-06']。
    跨月证据: 旧代码抓隐藏年图层(头「2021-2032」)翻页=年跳 → 18/18 未确认;
              改用可见月标题 + 单月 chevron 后探针 probe_flow_date6 打通。
    2026-10-15 任务 J 证据: 真机仍 18/18「未确认·输入框=['','']」(页面停在默认 2026-10-13)
              → 重写为「探测 → 键入(读回为准) → 日历点选兜底」; 本契约随之更新。
    """
    src = inspect.getsource(MeituanAdapter._set_flow_date)
    # 交互姿势: 控件靠 placeholder 认(本页两框默认空, 不能按"值像日期"找)
    assert "_FLOW_INPUTS_JS" in inspect.getsource(MeituanAdapter._flow_inputs) \
        and "开始时间" in MeituanAdapter._FLOW_INPUTS_JS, \
        "日期控件必须按 placeholder 开始/结束时间 探测(本页两框默认为空)"
    assert "self._flow_type_dates(" not in src, \
        "任务 M4: 流量页键入必败(探针读回恒 ['','']) → 调用已停用, 不许再回到调用方 (说明性文字可留)"
    assert "勿再调用" in inspect.getsource(MeituanAdapter._flow_type_dates), \
        "停用的 _flow_type_dates 要留 docstring 说明(勿再调用)"
    assert "_flow_verified" in src, "读回验证必须走统一的硬证据函数"
    assert "已选时间" in inspect.getsource(MeituanAdapter._flow_verified), \
        "第二证据(页面「已选时间：」文本)必须保留"
    assert "_flow_read" in src, "控制台/失败分支要能读到当前输入框值(取证)"
    # 任务 M4: 窗口前置判定(窗外 = 页面限制 → 一行日志秒退, 不翻月/不点击/不键入/不写重取证包)
    assert "_flow_date_reachable" in src, "必须**先**判窗口(窗外 = 客观取不到)"
    assert src.index("_flow_date_reachable") < src.index("_flow_open_custom"), \
        "判窗必须在翻月/开面板**之前**(逐店反复试探客观取不到的日期只是白烧时间)"
    assert "_flow_window_msg" in src and "宁缺勿假" in inspect.getsource(MeituanAdapter._flow_window_msg), \
        "窗外只留一行 warn 说清(商圈TOP10 留空·宁缺勿假)"
    assert "_flow_day_disabled" in src, "窗内也要防窗口边缘误判(目标日格全 disabled → 一行日志返回)"
    reach_bounds = inspect.getsource(mt._flow_window_bounds)
    assert "FLOW_WINDOW_DAYS" in reach_bounds and "days=1" in reach_bounds, \
        "窗口规则 = [今天-30, 今天-1](含边界)"
    # 日历兜底(点两次同一格 + Esc 清场重来)
    assert "_click_same_cell_twice" in src, "日历兜底必须走成对点选(同一格两击)"
    pair = inspect.getsource(MeituanAdapter._click_same_cell_twice)
    assert "_mark_day_cell" in pair, "成对点选要先标记目标格"
    assert pair.count("click") >= 2, "成对协议要**两击**(起点+终点)"
    assert "data-hm-day" in MeituanAdapter._MARK_DAY_JS, "要 JS 打标记 + locator 点击(合成 .click() 无效)"
    assert "data-hm-custom" in inspect.getsource(MeituanAdapter._flow_open_custom), \
        "「自定义」要打标记点击"
    assert "\\s+" in inspect.getsource(MeituanAdapter._flow_open_custom), \
        "「自定义」要归一化匹配(实际 innerText 是 自定义\\n~)"
    assert "_clear_picker" in src, "成对失败必须 Esc 清场重来(不在脏状态里继续试探)"
    assert "_day_candidates" in inspect.getsource(MeituanAdapter._mark_day_cell), \
        "格子候选要先走面板感知的 _day_candidates(双面板/邻月尾巴格)"
    assert "disabl" in MeituanAdapter._MARK_DAY_JS and "old" in MeituanAdapter._MARK_DAY_JS, \
        "退回标记路径也要跳过 disabled/邻月格"
    # 跨月(2026-10-10 修复的守卫)
    assert "_nav_month_roo" in src, "翻月要有独立步骤"
    nav = inspect.getsource(MeituanAdapter._nav_month_roo)
    assert "chevron-left" in nav and "chevron-right" in nav, "翻月只许用单月 chevron-left/right"
    assert "double" in nav, "必须显式排除 double-left/right(年跳, 实测跳错年)"
    assert "_flow_visible_month" in nav, "月标题必须按**可见性**读取(隐藏年图层头是干扰)"
    assert "range(36)" in nav, "翻月要有次数上限"
    assert "range(1, 3)" in src, "日历兜底要有轮次上限(不与下载页共用 range(3) 的旧口径)"
    assert "面板未能打开" in src, "打不开面板 → 留空并告警(宁可不写)"
    print("✅ 契约: 两态(键入/日历) + 同一格两击 + 归一化自定义 + 跳过禁用格 + 跨月单月chevron(排除年跳)")


def test_visible_month_parse():
    """_flow_visible_month 的解析: 中文数字月要认, 隐藏年图层干扰头(2021-2032)必须拒。"""
    ad = MeituanAdapter.__new__(MeituanAdapter)

    class P:
        def __init__(self, heads):
            self.heads = heads

        def evaluate(self, js, arg=None):
            return self.heads

    assert ad._flow_visible_month(P(["2026十月"])) == (2026, 10)
    assert ad._flow_visible_month(P(["2026九月", "2026十一月"])) == (2026, 9)
    assert ad._flow_visible_month(P(["2026十二月"])) == (2026, 12)
    assert ad._flow_visible_month(P(["2026一月"])) == (2026, 1)
    assert ad._flow_visible_month(P(["2021-2032"])) is None, "年图层干扰头不许被解析成月份"
    assert ad._flow_visible_month(P([])) is None
    print("✅ 可见月解析: 中文数字月(十/十一/十二/一~九)认; 干扰头/空 拒")


if __name__ == "__main__":
    main()
    test_m2_second_click_ladder()
    test_m2_relaxed_fallback()
    test_m2_date_pack()
    test_click_twice_contract()
    test_visible_month_parse()
