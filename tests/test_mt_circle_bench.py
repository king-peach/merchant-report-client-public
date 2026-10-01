# -*- coding: utf-8 -*-
"""美团商圈「对比基准」单测(fake page, 不启动浏览器)。

背景(2026-09-28 用户截图确认路径 + 真机症状): 流量转化页的对比基准下拉, 选项是
「商圈同行均值 / 商圈同行前10%均值」。旧实现只认一个很长的内部选择器; 更致命的是
capture_circle 把「业务切换成功」当前置条件, 切不过就丢掉整店数据 → 31 店 TOP 全空。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan as mt                          # noqa: E402


class FakeMouse:
    def __init__(self, log):
        self.log = log

    def click(self, x, y):
        self.log.append(("mouse_click", round(x), round(y)))


class FakeLoc:
    """locator(...).first.click() 的替身(新的定位方式: JS 打标记 + locator 点击)。"""

    def __init__(self, log, sel):
        self.log, self.sel = log, sel
        self.first = self

    def click(self, timeout=None):
        self.log.append(("locator_click", self.sel))


class FakePage:
    """bench=当前口径; has_control=对比基准下拉在不在; opt=有没有「前10%均值」; old_sel=旧选择器是否还有效。"""

    def __init__(self, bench="比前一日", has_control=True, opt=True, old_sel=True):
        self.bench = bench
        self.has_control = has_control
        self.opt = opt
        self.old_sel = old_sel
        self.log = []
        self.mouse = FakeMouse(self.log)

    def locator(self, sel):
        return FakeLoc(self.log, sel)

    def evaluate(self, js, arg=None):
        if "const want = norm" in js:                 # 标记「商圈同行前10%均值」选项
            self.log.append(("mark_option",))
            if not self.opt:
                return False
            self.bench = "商圈同行前10%均值"
            return True
        if "document.querySelectorAll('input,span,div')" in js:   # 标记对比基准下拉(打 data-hermes-bench)
            return 1 if self.has_control else 0
        if "funnel_indicator-wrapper" in js:          # 旧内部选择器读当前口径
            return self.bench if (self.has_control and self.old_sel) else ""
        if "商圈同行.*均值" in js:                     # 按文案扫当前口径
            return self.bench if self.has_control else ""
        raise AssertionError(f"未预期 JS: {js[:60]}")


def mk(page):
    ad = mt.MeituanAdapter.__new__(mt.MeituanAdapter)
    ad.sink = type("S", (), {"log": lambda self, m, level="info": page.log.append(("log", m))})()
    return ad


def logs(p):
    """日志里的 sink.log 文案(日志同时混有 mouse_click 之类 3 元组, 不能直接解构)。"""
    return [e[1] for e in p.log if isinstance(e, tuple) and len(e) == 2 and e[0] == "log"]


def main():
    # ① 读当前口径
    p = FakePage(bench="商圈同行均值")
    assert mk(p)._bench_now(p) == "商圈同行均值"
    print("✅ 读当前对比基准口径")

    # ② 已经是前10% → 一次都不点(幂等)
    p = FakePage(bench="商圈同行前10%均值")
    assert mk(p)._set_bench_top10(p) == "商圈同行前10%均值"
    assert not any(str(x[0]).endswith("click") for x in p.log), p.log
    print("✅ 已是前10%均值: 零点击直接返回")

    # ③ 默认口径 → 点开下拉 + 选中前10%均值(必须真的点到「前10%」那个选项, 不是只点开下拉)
    p = FakePage(bench="比前一日")
    ad = mk(p)
    got = ad._set_bench_top10(p)
    assert any(x[0] == "locator_click" and "hermes-bench" in str(x[1]) for x in p.log), p.log
    assert ("mark_option",) in p.log, p.log
    assert any(x[0] == "locator_click" and "hermes-bench-opt" in str(x[1]) for x in p.log), \
        f"必须点到「商圈同行前10%均值」选项本身: {p.log}"
    assert got == "商圈同行前10%均值", got
    print("✅ 默认口径 → 点下拉 + 选「商圈同行前10%均值」→ 读回前10%")

    # ④ 完全找不到下拉: 不崩、返回空口径、并打印取证
    p = FakePage(bench="比前一日", has_control=False)
    got = mk(p)._set_bench_top10(p)
    assert got == "", f"控件不存在时读不到口径, 应为空: {got!r}"
    assert any("没找到对比基准下拉" in str(m) for m in logs(p)), p.log
    print("✅ 没有该控件: 不崩 + 取证日志(返回空口径, 由调用方按默认口径记录)")

    # ④b 关键兜底: 旧内部选择器失效, 但**按文案**仍能找到控件和当前口径
    p = FakePage(bench="比前一日", old_sel=False)
    got = mk(p)._set_bench_top10(p)
    assert got == "商圈同行前10%均值", got
    assert any(x[0] == "locator_click" for x in p.log), p.log
    print("✅ 旧选择器失效 → 文案兜底仍完成切换(改版后最关键的一条)")

    # ⑤ 该店没有「前10%均值」选项: 保留默认口径(绝不冒充前10%)
    p = FakePage(bench="商圈同行均值", opt=False)
    got = mk(p)._set_bench_top10(p)
    assert got == "商圈同行均值", got
    assert any("无「商圈同行前10%均值」选项" in str(m) for m in logs(p)), p.log
    print("✅ 该店无前10%选项: 保留实际口径(不臆造)")

    # ⑥ 致命路径回归: capture_circle 不能再把「业务切换」当前置条件
    import inspect
    src = inspect.getsource(mt.MeituanAdapter.capture_circle)
    assert "_set_flow_biz" not in src, "capture_circle 不应再依赖业务切换(切不过会丢整店数据)"
    print("✅ 回归: capture_circle 不再以「业务切换成功」为前置条件")

    # ⑦ 硬门禁: 只有「商圈同行」口径的右值才算 TOP, 别的口径(比前一日/比上周)一律不写
    A = mt.MeituanAdapter
    for good in ("商圈同行前10%均值", "商圈同行均值", "商圈同行前10%均值(本店所在商圈)"):
        assert A._bench_is_circle(good) is True, good
    for bad in ("比前一日", "比上周二", "", None, "昨日同比"):
        assert A._bench_is_circle(bad) is False, bad
    assert "self._bench_is_circle(_bench)" in src, "capture_circle 必须用该门禁过滤"
    print("✅ 硬门禁: 只认商圈同行口径, 对比基准不对时宁可留空也不写错数据")

    print("✅ 美团商圈对比基准单测全部通过")


if __name__ == "__main__":
    main()
