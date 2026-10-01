# -*- coding: utf-8 -*-
"""淘宝闪购「自定义选单日」单测(fake frame, 不启动浏览器)。

覆盖 2026-09-24 真机定位到的三个机制(改版后的 antd 区间选择器):
  ① 触发「自定义」必须点日期控件那一排的 radio(页面上别处也有同名元素);
  ② 区间选择器要 **同一格点两次**(起点+终点)才算单日;
  ③ 「自定义」已是选中态时再点不弹层 → 需先切预设打破选中态。
另加两条安全约束: 禁用格不硬点; 选完必须校验「已选时间」, 不一致就失败(绝不静默继续)。
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import taobao as tb      # noqa: E402


class FakeCell:
    def __init__(self, title, state, disabled=False, visible=True):
        self.title = title
        self.state = state
        self.disabled = disabled
        self.visible = visible

    def is_visible(self):
        return self.visible

    def evaluate(self, _js):
        return self.disabled

    def click(self, timeout=None):
        self.state["log"].append(("cell_click", self.title))
        self.state["clicks"] = self.state.get("clicks", 0) + 1
        if self.state.get("select_on_click") and self.state["clicks"] >= 2:
            d = self.title[5:]
            self.state["sel_text"] = f"已选时间：{d}至{d} (1日)"


class FakeRadio:
    def __init__(self, text, state):
        self.text = text
        self.state = state

    def click(self, timeout=None):
        self.state["log"].append(("radio_click", self.text))
        if self.text == "自定义":
            if not self.state.get("custom_active"):
                self.state["panel_open"] = True      # 未选中态 → 点它弹层
            # 已选中态 → 不弹层(真机行为), 依赖调用方先切预设
        elif self.text == "昨日":
            self.state["custom_active"] = False      # 切预设 = 打破「自定义」选中态
            self.state["panel_open"] = False

    def click_timeout(self):                         # pragma: no cover
        return None


class FakeLocator:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]

    @property
    def first(self):
        return self.items[0]

    def click(self, timeout=None):
        self.items[0].click(timeout)


class FakeFrame:
    def __init__(self, state):
        self.state = state

    def evaluate(self, js, arg=None):
        s = self.state
        if "document.body.innerText" in js:
            return s["sel_text"]
        if "ant-picker-dropdown" in js and "getBoundingClientRect" in js:
            return 40 if s["panel_open"] else 0
        if "ant-picker-header-view" in js:
            return s["headers"]
        raise AssertionError(f"未预期的 evaluate: {js[:50]}")

    def locator(self, sel, has_text=None):
        if has_text is not None:
            s = self.state
            if not s.get("has_radio", True):
                return FakeLocator([])
            return FakeLocator([FakeRadio(has_text, s)])
        title = sel.split('title="')[1].split('"')[0]
        return FakeLocator([c for c in self.state["cells"] if c.title == title])


class _Page:
    def __init__(self, frame):
        self.frames = [frame]


class _Ctx:
    def __init__(self, frame):
        # _calendar_frame() 会遍历 ctx.pages → page.frames
        self.pages = [_Page(frame)]


def make(sel_text="已选时间：09-23 周三", custom_active=False, panel_open=False,
         cell_disabled=False, select_on_click=True, has_radio=True, with_cell=True,
         headers=None):
    state = {"sel_text": sel_text, "log": [], "clicks": 0, "custom_active": custom_active,
             "panel_open": panel_open, "headers": headers or ["2026年9月", "2026年10月"],
             "has_radio": has_radio, "select_on_click": select_on_click, "cells": []}
    if with_cell:
        state["cells"] = [FakeCell("2026-09-21", state, disabled=cell_disabled)]
    fr = FakeFrame(state)
    ad = tb.TaobaoAdapter.__new__(tb.TaobaoAdapter)     # 不走 __init__(不需要浏览器)
    ad.ctx = _Ctx(fr)
    ad.page = _Page(fr)
    ad.sink = type("S", (), {"log": lambda self, m, level="info": state["log"].append(("log", m))})()
    return ad, fr, state


def main():
    # ① 正常切单日: 点一次「自定义」→ 同一格点**两次**
    ad, f, st = make()
    assert ad._pick_single_day(f, "2026-09-21") is True, "应成功"
    assert ("radio_click", "自定义") in st["log"], "必须点日期控件里的「自定义」"
    assert st["clicks"] == 2, f"区间选择器必须同一格点两次, 实际 {st['clicks']} 次"
    assert ad._read_selected_range(f) == ("09-21", "09-21"), ad._read_selected_range(f)
    print("✅ 正常单日: 点自定义 → 同一格点两次 → 已选时间校验通过")

    # ② 幂等: 已经是这天 → 一次都不点(省一次弹层交互)
    ad, f, st = make(sel_text="已选时间：09-21至09-21 (1日)")
    assert ad._pick_single_day(f, "2026-09-21") is True
    assert st["log"] == [], f"已选中不该有任何点击, 实际 {st['log']}"
    print("✅ 幂等: 已是目标日 → 零点击直接返回")

    # ③ 「自定义」已是选中态(点它不弹层) → 先切预设打破选中态再点
    ad, f, st = make(custom_active=True)
    assert ad._pick_single_day(f, "2026-09-21") is True, "选中态下也必须能切成功"
    kinds = [x for x in st["log"] if x[0] == "radio_click"]
    assert ("radio_click", "昨日") in kinds, f"应先切预设打破选中态: {kinds}"
    assert kinds.count(("radio_click", "自定义")) >= 2, f"选中态下应再点一次「自定义」: {kinds}"
    # 第一次点自定义不弹层 → 点昨日打破 → 再点自定义才成功
    second_custom = kinds.index(("radio_click", "自定义"), 1)
    assert kinds.index(("radio_click", "昨日")) < second_custom, f"顺序应为 自定义→昨日→自定义: {kinds}"
    print("✅ 选中态兜底: 先点「昨日」打破选中态 → 再点「自定义」→ 成功")

    # ④ 禁用格(超出可选范围) → 直接失败并说明, 不硬点
    ad, f, st = make(cell_disabled=True)
    assert ad._pick_single_day(f, "2026-09-21") is False
    assert st["clicks"] == 0, "禁用格不能被点击"
    assert any("不可选" in m for _, m in st["log"] if isinstance(m, str)), st["log"]
    print("✅ 禁用格: 不点击, 明确报「不可选」")

    # ⑤ 点了但「已选时间」没变 → 必须失败(绝不能带着错的日期继续)
    ad, f, st = make(select_on_click=False)
    assert ad._pick_single_day(f, "2026-09-21") is False, "选完没变必须失败"
    assert any("未变" in m for _, m in st["log"] if isinstance(m, str)), st["log"]
    print("✅ 安全约束: 已选时间未变 → 返回 False(不静默继续)")

    # ⑥ 日历压根打不开 → 失败, 不能假装切成功
    ad, f, st = make(has_radio=False)
    assert ad._pick_single_day(f, "2026-09-21") is False
    assert any("自定义" in m for _, m in st["log"] if isinstance(m, str)), st["log"]
    print("✅ 打不开日历: 明确失败")

    # ⑦ 目标日在别的月份 → 触发翻页(目标月不在可视两栏内)
    ad, f, st = make(with_cell=False, headers=["2026年9月", "2026年10月"])
    assert ad._pick_single_day(f, "2026-11-05") is False      # 无格子 + 会翻页, 最终失败
    print("✅ 跨月: 按年月差触发翻页(找不到格子时明确失败)")

    print("✅ 淘宝「自定义选单日」单测全部通过")


if __name__ == "__main__":
    main()
