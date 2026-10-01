# -*- coding: utf-8 -*-
"""流量页(商圈采集)日期: 必须拿到硬证据才算生效; 未确认则**放弃该店**(留空)。

真机背景(2026-10-02 用户报"美团外卖没有根据报表区间的时间来处理, 默认选的昨日"):
  日志 `📅 流量页日期 → 2026-09-30（未确认）` 之后照抓 → 商圈TOP 列写的是默认日期的数,
  数字看着正常但日期是错的(最危险的错法)。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan as mt                           # noqa: E402


class FakeInputs:
    def __init__(self, page, values):
        self.page, self.values, self._i = page, values, 0

    def count(self):
        return len(self.values)

    def nth(self, i):
        self._i = i
        return self

    def fill(self, v):
        self.values[self._i] = ""

    def type(self, v, delay=None):
        if self.page.accepts:                     # 页面接受输入才写进去
            self.values[self._i] = v

    def input_value(self):
        return self.values[self._i]


class FakePage:
    """accepts=False 模拟"页面没吃下我们输入的日期"(还停在默认昨日)。"""

    def __init__(self, accepts=True, values=None, body=""):
        self.accepts = accepts
        self.values = values if values is not None else ["2026-09-29", "2026-09-29"]
        self.body = body
        self.url = "https://waimaieapp.meituan.com/flow"

    def evaluate(self, js, arg=None):
        if "document.body?document.body.innerText" in js:
            return self.body
        return True

    def locator(self, sel):
        return FakeInputs(self, self.values)

    class keyboard:                                # noqa: N801
        @staticmethod
        def press(k):
            return None


def mk(page):
    ad = mt.MeituanAdapter.__new__(mt.MeituanAdapter)
    logs = []
    ad.page = page
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append((level, m))})()
    return ad, logs


def main():
    # ① 输入框真的拿到了目标日期 → 生效
    p = FakePage(accepts=True)
    ad, logs = mk(p)
    assert ad._set_flow_date(p, "2026-09-30") is True, logs
    assert p.values == ["2026-09-30", "2026-09-30"], p.values
    assert any("已生效" in m for _, m in logs), logs
    print("✅ 输入框拿到目标日期 → 判「已生效」")

    # ② 页面没吃下输入(还是昨日) → 未确认, 且必须留下取证
    p = FakePage(accepts=False, values=["2026-09-29", "2026-09-29"], body="今日 昨日 自定义")
    ad, logs = mk(p)
    assert ad._set_flow_date(p, "2026-09-30") is False, logs
    assert any("未确认" in m for _, m in logs), logs
    assert any("取证" in m for _, m in logs), logs
    print("✅ 页面没吃下输入(仍是昨日) → 判「未确认」+ 留下取证")

    # ③ 页面文本里有硬标记(输入框读不到时的第二证据)
    p = FakePage(accepts=False, values=["", ""], body="已选时间：2026/09/30 自定义")
    ad, logs = mk(p)
    assert ad._set_flow_date(p, "2026-09-30") is True, logs
    print("✅ 页面标记「已选时间：2026/09/30」(斜杠写法) → 也算生效")

    # ④ 关键: 调用方(capture_circle)必须在未确认时放弃该店, 不能继续抓
    src = inspect.getsource(mt.MeituanAdapter.capture_circle)
    assert "if not self._set_flow_date(page, date)" in src, "capture_circle 必须检查日期是否确认"
    assert "该店商圈列留空" in src, "未确认时应放弃该店(留空)而不是照抓"
    print("✅ capture_circle: 日期未确认 → 放弃该店(商圈列留空), 不拿默认日期冒充")

    print("✅ 流量页日期(硬证据验收 + 未确认即放弃)单测全部通过")


if __name__ == "__main__":
    main()
