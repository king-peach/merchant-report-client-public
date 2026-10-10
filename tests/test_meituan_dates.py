# -*- coding: utf-8 -*-
"""美团下载页日期(roo 双面板)修复单测: 表头「2026 十月」中文数字月解析 + 关键选择器守卫。

真机事实(2026-10-09): 面板头 innerText 形如「2026十月」(中文数字); 可点的日子 <td> 可能
**没有 class**(禁用格是 day disabled); 翻月按钮 = i.roo-icon-chevron-left/right-new(双面板取最左)。
旧代码只认「2026年10月」格式 → 翻月逻辑从不启动 → 跨月(如 09-24)永远设不上。
"""
import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_HERE, "kernel"))

from merchant_report.platforms.meituan import MeituanAdapter  # noqa: E402


class FakePage:
    def __init__(self, text=None, raise_exc=False):
        self._text = text
        self._raise = raise_exc

    def evaluate(self, _js):
        if self._raise:
            raise RuntimeError("boom")
        return self._text


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

    src = open(os.path.join(_HERE, "kernel/merchant_report/platforms/meituan.py"), encoding="utf-8").read()
    for needle in ("chevron-left-new", "chevron-right-new", ".roo-datepicker-body td",
                   "_CN_MONTH", "roo-datepicker-header", "indexOf('disabled')"):
        assert needle in src, f"源码缺少关键标志: {needle}"
    assert "td.day a" not in src, "旧选择器 td.day a 残留(可点格可能无 class)"
    print("✅ 关键选择器守卫通过")

    # _click_day: 面板月份不对→翻月→点格; 用 fake 面板跟踪月份驱动(不碰浏览器)
    class _Mouse:
        def click(self, x, y):
            pass

    class FPage:
        def __init__(self, month):
            self.month = month
            self.mouse = _Mouse()

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
    print("✅ _click_day 月份驱动通过")

    print("✅ 全部通过 (美团日期控件修复)")


if __name__ == "__main__":
    main()
