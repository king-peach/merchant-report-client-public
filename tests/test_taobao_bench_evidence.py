# -*- coding: utf-8 -*-
"""D6 复核: 淘宝「对比对象=商圈同行前10%均值」链路取证单测(不改抓取主逻辑)。

规格书 §4.6 硬门禁: 基准**切不成 → TOP 列留空**(宁可不写也不写错)。
本单测锁死两件事:
  ① 切不成 → _pick_bench 返回 False(上层据此令 top10 为空 → 写空), **不允许**"失败当成功";
  ② 真机排查取证: 成功/失败都要有可 grep 的日志(当前基准 / 尝试序列 / 失败原因)。
"""
import inspect
import os
import pathlib
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kernel"))
from merchant_report.platforms.taobao import TaobaoAdapter                        # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


class Log:
    def __init__(self):
        self.lines = []

    def log(self, msg, level="info"):
        self.lines.append((level, msg))


class FakeKeyboard:
    def press(self, k):
        self.last = k


class FakeMouse:
    """_bench_reset 的空白点击: 记录坐标(取证"重试前确实清场了")。"""

    def __init__(self):
        self.clicks = []

    def click(self, x, y=None):
        self.clicks.append((x, y))


class FakeLoc:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel
        self.first = self

    def count(self):
        # 入口文案存在 → 1; 目标选项存在与否由 page.has_option 控制
        if "商圈同行前10%均值" in self.sel:
            return 1 if self.page.has_option else 0
        return 1 if self.page.has_entry else 0

    def click(self, timeout=None):
        self.page.clicks.append(self.sel)
        self.page.timeouts.append(timeout)
        if "商圈同行前10%均值" in self.sel and self.page.has_option:
            self.page.bench = "商圈同行前10%均值"      # 点中了才算切成功


class FakeFrame:
    """cust = 漏斗当前基准; has_entry=基准入口在不在; has_option=下拉里有前10%选项。"""

    def __init__(self, cust="比上周同时段", has_entry=True, has_option=True, options=None):
        self.bench = cust
        self.has_entry = has_entry
        self.has_option = has_option
        self.options = options if options is not None else ["商圈同行均值", "商圈同行前10%均值"]
        self.clicks = []
        self.timeouts = []
        self.keyboard = FakeKeyboard()
        self.mouse = FakeMouse()

    def locator(self, sel):
        return FakeLoc(self, sel)

    def evaluate(self, js, arg=None):
        # _parse_funnel: 只关心 cust 段
        if "流量转化" in js:
            return {"cust": self.bench, "ftype": "外卖", "nums": {}, "rates": {}}
        if "dropdown" in js:                    # _bench_dropdown_options(失败取证: 实际选项)
            return list(self.options)
        return None


def mk(frame):
    a = TaobaoAdapter.__new__(TaobaoAdapter)      # 不跑 __init__(不碰浏览器)
    a.sink = Log()
    return a, a.sink


def main():
    print("【1】已是前10% → 直接成功并日志取证")
    f = FakeFrame(cust="商圈同行前10%均值")
    a, lg = mk(f)
    check("返回 True", a._pick_bench(f) is True)
    check("日志出现 📊 商圈口径 → 商圈同行前10%均值",
          any(m.startswith("📊 商圈口径 → 商圈同行前10%均值") for _, m in lg.lines),
          str(lg.lines))

    print("【2】能切 → 切成功后返回 True, 日志含尝试序列")
    f2 = FakeFrame(cust="比上周同时段", has_entry=True, has_option=True)
    a2, lg2 = mk(f2)
    check("返回 True", a2._pick_bench(f2) is True)
    check("基准真被切成前10%", f2.bench == "商圈同行前10%均值", f2.bench)
    check("日志含「切成功」与尝试序列",
          any("切成功" in m and "尝试序列" in m for _, m in lg2.lines), str(lg2.lines))

    print("【3】切不成(下拉没有该选项) → 返回 False + warn(上层据此 TOP 列留空)")
    f3 = FakeFrame(cust="比上周同时段", has_entry=True, has_option=False)
    a3, lg3 = mk(f3)
    check("返回 False(=硬门禁: 留空)", a3._pick_bench(f3) is False)
    check("基准没被改", f3.bench == "比上周同时段", f3.bench)
    check("warn 文案说明 TOP 列留空",
          any(lv == "warn" and "TOP" in m and "留空" in m for lv, m in lg3.lines), str(lg3.lines))
    check("warn 文案含尝试序列(真机排查用)",
          any(lv == "warn" and "尝试序列" in m for lv, m in lg3.lines), str(lg3.lines))

    print("【4】页面根本没有基准入口 → False + warn(不静默)")
    f4 = FakeFrame(cust="新客", has_entry=False, has_option=False)
    a4, lg4 = mk(f4)
    check("返回 False", a4._pick_bench(f4) is False)
    check("warn 有留空说明", any(lv == "warn" and "留空" in m for lv, m in lg4.lines), str(lg4.lines))

    # ── 2026-10-15 任务 J: 切基准加固(真机 27 次 `切基准 超时`, 全部 timeout=5000ms) ──
    print("【5】切基准加固: 点击超时 5s→10s + 失败留实际下拉选项 + 重试前清场(仅限切基准一步)")
    f5 = FakeFrame(cust="比上周同时段", has_entry=True, has_option=False,
                   options=["商圈同行均值", "商圈同城均值", "比上周同时段"])
    a5, lg5 = mk(f5)
    check("切不成仍返回 False(硬门禁不变)", a5._pick_bench(f5) is False)
    check("基准没被改(切不成绝不冒充)", f5.bench == "比上周同时段", f5.bench)
    check("点击超时已抬到 10000ms", f5.timeouts and min(f5.timeouts) == 10000, str(f5.timeouts))
    check("失败日志带出**实际下拉选项**(有证据才能改选择器)",
          any("实际选项" in m and "商圈同城均值" in m for _, m in lg5.lines), str(lg5.lines))
    check("整段重试 2 轮(日志可见第 2 次)",
          any("重试" in m and "第 2/2" in m for _, m in lg5.lines), str(lg5.lines))
    check("重试前清场(Esc + 点空白, 关掉残留下拉)",
          f5.keyboard.last == "Escape" and f5.mouse.clicks, f"{f5.keyboard.last}/{f5.mouse.clicks}")
    src5 = inspect.getsource(TaobaoAdapter._pick_bench)
    check("重试轮次有上限(range 1,3)", "range(1, 3)" in src5, src5[:0])
    check("清场走独立函数 _bench_reset", "_bench_reset" in src5)
    check("失败取证走独立函数 _bench_dropdown_options", "_bench_dropdown_options" in src5)
    check("清场用 Esc + 空白点击(不是再点一次同一个入口)",
          "Escape" in inspect.getsource(TaobaoAdapter._bench_reset)
          and "mouse.click" in inspect.getsource(TaobaoAdapter._bench_reset))
    print("✅ 切基准加固: 超时 10s / 2 轮重试 / 清场 / 下拉选项取证, 且切不成仍留空")

    print("【6】上游接线: ok=bench in cust → TOP 列留空的硬门禁仍在")
    src = pathlib.Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kernel",
                                    "merchant_report", "platforms", "taobao.py")).read_text(encoding="utf-8")
    check("capture_per_store 里 ok = bench in cust", "ok = bench in cust" in src)
    check("top10 仅在 ok 时装配", "top10 = tk if ok else {}" in src)
    check("zb_top 仅在 ok 时装配", "zb_top = tk if ok else {}" in src)
    # 写侧: top10 为空 → DN 等留空(build_write_set 照收 None)
    from merchant_report.platforms import taobao_mapping as tm
    ws = tm.build_write_set({"曝光人数": 1, "进店人数": 1, "下单人数": 1}, {},
                            {"外卖_全部顾客": {"shop": {}, "top10": {}},
                             "爆品团_全部顾客": {"shop": {}, "top10": {}}})
    check("top10={} → TOP 列(DN/DQ/DW)全空",
          all(ws.get(c) is None for c in ("DN", "DQ", "DW")), str({c: ws.get(c) for c in ("DN", "DQ", "DW")}))

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
