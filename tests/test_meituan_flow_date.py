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
        return None


class FakePage:
    """accepts=False 模拟"页面没吃下我们的日期选择"(还停在默认昨日)。

    - clicks 计数模拟\"点两次日格子\"(点满 2 次且 accepts=True 时页面才把日期写进输入框);
    - months = 可见月标题列表(可动态改), 用于驱动**跨月翻页**测试: 点单月 chevron 时翻一页。
    """

    def __init__(self, accepts=True, values=None, body="", day_cells=1, months=None):
        self.accepts = accepts
        self.values = values if values is not None else ["2026-09-29", "2026-09-29"]
        self.body = body
        self.url = "https://waimaieapp.meituan.com/flow"
        self.clicks = 0
        self.day_cells = day_cells
        self.months = months if months is not None else ["2026九月", "2026十月"]
        self.nav_clicks = 0
        self.mouse_clicks = 0
        self.mouse = FakeMouse(self)

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
    print("✅ 跨月: 2026十月 →(单月chevron)→ 2026九月 → 点两次格子 → 生效")

    # ⑤ 关键: 调用方(capture_circle)必须在未确认时放弃该店, 不能继续抓
    src = inspect.getsource(mt.MeituanAdapter.capture_circle)
    assert "if not self._set_flow_date(page, date)" in src, "capture_circle 必须检查日期是否确认"
    assert "该店商圈列留空" in src, "未确认时应放弃该店(留空)而不是照抓"
    print("✅ capture_circle: 日期未确认 → 放弃该店(商圈列留空), 不拿默认日期冒充")

    print("✅ 流量页日期(硬证据验收 + 未确认即放弃 + 跨月翻页)单测全部通过")


def test_click_twice_contract():
    """锁住 2026-10-08/2026-10-10 的真根因, 防止退化。

    真机证据: 点1次后 已选时间仍是 2026-10-07 / 输入框 ['',''];
              点2次后 已选时间 2026-10-06 / 输入框 ['2026-10-06','2026-10-06']。
    跨月证据: 旧代码抓隐藏年图层(头「2021-2032」)翻页=年跳 → 18/18 未确认;
              改用可见月标题 + 单月 chevron 后探针 probe_flow_date6 打通。
    """
    src = inspect.getsource(MeituanAdapter._set_flow_date)
    assert "range(3)" in src, "点格子最多三次(起点+终点, 余量给面板重渲染)"
    assert "data-hm-day" in src, "要 JS 打标记 + locator 点击(合成 .click() 无效)"
    assert "data-hm-custom" in src, "「自定义」要打标记点击"
    assert "\\s+" in src, "「自定义」要归一化匹配(实际 innerText 是 自定义\\n~)"
    assert "disabled" in src and "old" in src, "要跳过 disabled(未来)/old(上月) 格子; 且必须看 td 自身 class"
    assert "input_value" in src and "已选时间" in src, "双证据验收必须保留"
    assert ".type(" not in src, "不许退回「往输入框键入日期」(roo 受控组件不吃, 已实测无效)"
    # 2026-10-08 真机 trace: 点「自定义」后面板**已自动展开**(日格=84); 再点输入框会把面板关掉(日格=0)。
    assert 'locator("input[placeholder=\'开始时间\']").first.click' not in src, \
        "不许再点输入框(会把已展开的面板关掉 —— 真机实测 日格 84→0)"
    assert "offsetParent !== null" in src, "面板/格子要用 offsetParent 判可见(隐藏年份面板有同名 class)"
    assert "for (const p of panels)" in src and "if (cells.length) break" in src, \
        "要在可见面板里逐面板找格子(不再只看 panels[0])"
    # 2026-10-10 跨月修复的守卫
    assert "chevron-left" in src and "chevron-right" in src, "翻月只许用单月 chevron-left/right"
    assert "double" in src, "必须显式排除 double-left/right(年跳, 实测跳错年)"
    assert "_flow_visible_month" in src, "月标题必须按**可见性**读取(隐藏年图层头是干扰)"
    assert "range(36)" in src, "翻月要有次数上限"
    assert "面板未能打开" in src, "打不开面板 → 留空并告警(宁可不写)"
    print("✅ 契约: 点两次 + 归一化匹配自定义 + 跳过禁用格 + 双证据验收 + 跨月单月chevron(排除年跳)")


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
    test_click_twice_contract()
    test_visible_month_parse()
