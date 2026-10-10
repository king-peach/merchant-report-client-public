# -*- coding: utf-8 -*-
"""京东日期(antd 双月面板)修复单测: 月份解析 + 翻月方向(fake 页面驱动真实函数)。

真机背景(2026-10-09): 旧版只会点「下一页」→ 目标在过去(09-24)时越翻越远(翻到 2027-04),
最后在不可见格 td[title=...] 上 30s 超时 —— 用户报「京东卡在选日期」。
新逻辑: 读面板当月 → 按方向翻月 → 目标格 in-view 且可见才点; 禁用格直接报错。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import jd as jd_mod  # noqa: E402
from merchant_report.platforms.jd import JDAdapter  # noqa: E402

jd_mod.time.sleep = lambda *a, **k: None  # 测试瞬时化(仅本进程)


class _Pick:
    def is_visible(self):
        return True

    def scroll_into_view_if_needed(self, timeout=None):
        pass

    def click(self, timeout=None):
        pass


class _Cands:
    def __init__(self):
        self._p = _Pick()

    def count(self):
        return 1

    def nth(self, i):
        return self._p


class _Tgt:
    def locator(self, sel):
        assert ".ant-picker-range" in sel
        return _Cands()

    def evaluate(self, js, *a):
        return None


class _FakeLoc:
    """单元素定位器: count/is_visible/get_attribute/click + .first/.last 穿透。"""

    def __init__(self, frame, sel):
        self.frame, self.sel = frame, sel
        self.first = self
        self.last = self

    def _cell(self):
        import re as _re
        m = _re.search(r"title='([^']+)'", self.sel)
        return m.group(1) if m else None

    def count(self):
        d = self._cell()
        if d is None:
            return 1
        y, m = (int(x) for x in d.split("-")[:2])
        return 1 if self.frame.month == (y, m) else 0

    def is_visible(self):
        return self.count() > 0

    def get_attribute(self, k):
        d = self._cell()
        y, m = (int(x) for x in d.split("-")[:2])
        cls = "ant-picker-cell ant-picker-cell-in-view"
        if self.frame.month == (y, m) and self.frame.disabled:
            cls += " ant-picker-cell-disabled"
        return cls

    def click(self, timeout=None):
        d = self._cell()
        if d is not None:
            self.frame.actions.append(("cell", d))
            return
        which = "prev" if "prev" in self.sel else "next"
        self.frame.actions.append((which,))
        y, m = self.frame.month
        if which == "prev":
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        else:
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        self.frame.month = (y, m)


class _PanelFrame:
    """模拟挂日期面板的 frame: 双月面板简化为『左面板月』单状态。"""

    def __init__(self, month, disabled=False, td_count=30):
        self.month = month
        self.disabled = disabled
        self.td_count = td_count
        self.actions = []

    def locator(self, sel):
        return _FakeLoc(self, sel)

    def evaluate(self, js):
        if "ant-picker-cell-in-view" in js and "title" in js:   # _read_panel_ym
            return f"{self.month[0]}-{self.month[1]:02d}-01"
        if "td[title]" in js:                                    # 面板探测
            return self.td_count
        return None


class _ContentFrame:
    def evaluate(self, js):
        return 0


class _Page:
    def __init__(self, panel):
        self.frames = [_ContentFrame(), panel]


class _DummySink:
    def log(self, *a, **k):
        pass

    def need_human(self, *a, **k):
        pass

    def step_fail(self, *a, **k):
        pass


def _call(panel, df, dt):
    ad = JDAdapter.__new__(JDAdapter)
    ad.sink = _DummySink()
    return ad._set_dates_by_calendar(_Tgt(), _Page(panel), df, dt)


def main():
    ad = JDAdapter.__new__(JDAdapter)

    # ① 月份解析
    cases = [("2026-10-01", (2026, 10)), ("2026-09-24", (2026, 9)), ("2026年10月", (2026, 10)),
             ("2026年 10月", (2026, 10)), ("2027年4月", (2027, 4)), ("2025-12-15", (2025, 12)),
             ("2026", None), ("", None), (None, None), ("加载中", None)]
    for s, want in cases:
        got = ad._parse_ym(s)
        assert got == want, f"_parse_ym({s!r}): 期望 {want}, 实际 {got}"
    print("① 月份解析 全通过 ✓")

    # ② 跨月向后(用户真机场景): 面板 2026-10 → 目标 2026-09-24 → 『上一页』×1 + 点格×2
    p = _PanelFrame((2026, 10))
    _call(p, "2026-09-24", "2026-09-24")
    kinds = [a[0] for a in p.actions]
    assert p.month == (2026, 9), p.month
    assert kinds.count("prev") == 1 and "next" not in kinds and kinds.count("cell") == 2, p.actions
    print("② 跨月向后(09-24): prev×1 + 点格×2 ✓", p.actions)

    # ③ 跨年向后: 2026-10 → 2025-12-15 (10 个月) → prev×10, 绝不能出现 next
    p = _PanelFrame((2026, 10))
    _call(p, "2025-12-15", "2025-12-15")
    kinds = [a[0] for a in p.actions]
    assert p.month == (2025, 12), p.month
    assert kinds.count("prev") == 10 and "next" not in kinds, p.actions
    print("③ 跨年向后(2025-12-15): prev×10 无 next ✓")

    # ④ 向前(未来日期): 2026-10 → 2026-12 → next×2
    p = _PanelFrame((2026, 10))
    _call(p, "2026-12-03", "2026-12-03")
    kinds = [a[0] for a in p.actions]
    assert kinds.count("next") == 2 and "prev" not in kinds, p.actions
    print("④ 向前(12-03): next×2 无 prev ✓")

    # ⑤ 同月直选: 零翻月
    p = _PanelFrame((2026, 10))
    _call(p, "2026-10-06", "2026-10-06")
    kinds = [a[0] for a in p.actions]
    assert "next" not in kinds and "prev" not in kinds, p.actions
    print("⑤ 同月直选: 零翻月 ✓")

    # ⑥ 禁用格 → 明确报错(不闷等 30s)
    p = _PanelFrame((2026, 9), disabled=True)
    try:
        _call(p, "2026-09-24", "2026-09-24")
        raise AssertionError("应报错")
    except RuntimeError as e:
        assert "禁用" in str(e), e
    print("⑥ 禁用格 → 明确报错 ✓")

    print("\n✅ 京东日期修复 全绿")


if __name__ == "__main__":
    main()
