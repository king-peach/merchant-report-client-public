# -*- coding: utf-8 -*-
"""美团管家「日期选择」根治单测(fake page, 不启动浏览器)。

2026-10-09 真机取证(v1/v2 探针) 的机制:
  ① 面板打开后才能翻月; **第一次点击(起点)后视图会跳回当前月** → 每次点击前必须重新翻月;
  ② 格子用**中文全日期 title** 精确匹配(`td[title='2026年9月24日']`) —— 旧版只按日号匹配
     (跨月时点到错误月/未来格, 静默继续 → 数据被写进错误日期);
  ③ 单日 = 同一格点两次(起点+终点); 完成后面板自动关闭、双框都变成目标日;
  ④ **读回校验 = 两个只读输入框值必须 == 目标** —— 不符重试一轮, 再不符**抛错**(宁可不写)。
"""
import pathlib
import sys
import time as _time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan_gj as gj   # noqa: E402

_time.sleep = lambda *_a, **_k: None          # 单测禁睡眠(生产代码用 time.sleep 节流)


def _shift_month(head, d):
    y, m = int(head[:4]), int(head[5:head.index("月")])
    m += d
    if m < 1:
        y, m = y - 1, 12
    if m > 12:
        y, m = y + 1, 1
    return f"{y}年{m}月"


def _pick(st, title):
    """真机行为: 第1击=起点(视图跳回当前月); 第2击=终点(面板关闭)。noop 模式=点了没反应。"""
    if st["mode"] == "noop":
        return
    y, m, d = (int(x) for x in title.replace("年", "-").replace("月", "-").replace("日", "").split("-"))
    disp = f"{y}/{m:02d}/{d:02d}"
    if st["phase"] == 0:
        st["inputs"][0] = disp
        st["head"] = st["current_month"]          # 真机: 选完起点视图跳回当前月
        st["phase"] = 1
    else:
        st["inputs"][1] = disp
        st["panel_open"] = False
        st["phase"] = 0


class FakeMouse:
    def __init__(self, st):
        self.st = st

    def click(self, x, y):
        st = self.st
        st["mouse"].append((x, y))
        if y == 10:                               # 翻月按钮(x=9 上个月 / x=11 下个月)
            st["head"] = _shift_month(st["head"], x - 10)
            st["nav_clicks"].append(st["head"])
            return
        for title, (cx, cy) in st["cells"].items():
            if (x, y) == (cx, cy):
                st["cell_clicks"].append(title)
                _pick(st, title)
                return
        st["stray_clicks"].append((x, y))


class _InputLocator:
    def __init__(self, st):
        self.st = st

    def is_visible(self):
        return self.st["mode"] != "noinput"

    def click(self, timeout=None):
        self.st["input_clicks"] += 1
        if self.st["mode"] not in ("noopen", "noinput"):
            self.st["panel_open"] = True


class _Locator:
    def __init__(self, st):
        self.first = _InputLocator(st)


class FakePage:
    def __init__(self, st):
        self.st = st
        self.mouse = FakeMouse(st)

    def locator(self, *_a, **_k):
        return _Locator(self.st)

    def evaluate(self, js, arg=None):
        st = self.st
        if "/*GJ:inputs*/" in js:
            return list(st["inputs"])
        if "/*GJ:panel-open*/" in js:
            return st["panel_open"]
        if "/*GJ:head*/" in js:
            return st["head"] if st["panel_open"] else None
        if "/*GJ:navbtn*/" in js:
            return {"x": 10 + arg, "y": 10} if st["panel_open"] else None
        if "/*GJ:cell*/" in js:
            if st["panel_open"] and arg in st["cells"]:
                cx, cy = st["cells"][arg]
                return {"x": cx, "y": cy}
            return None
        if "out.sort((p, q) => p.w - q.w)" in js:   # 「查询」等文本按钮探测: 本环境没有 → None
            return None
        raise AssertionError(f"未预期 evaluate: {js[:70]}")


class FakeSink:
    def __init__(self):
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))


def make(mode="ok"):
    st = {"mode": mode, "phase": 0, "panel_open": False,
          "head": "2026年10月", "current_month": "2026年10月",
          "inputs": ["2026/10/09", "2026/10/09"],
          "cells": {"2026年9月24日": (456, 393), "2026年10月6日": (500, 400), "2026年10月9日": (474, 318)},
          "mouse": [], "nav_clicks": [], "cell_clicks": [], "stray_clicks": [], "input_clicks": 0}
    ad = gj.MeituanGjAdapter.__new__(gj.MeituanGjAdapter)   # 不走 __init__(不需要浏览器)
    ad.sink = FakeSink()
    return ad, FakePage(st), st


def main():
    # ⓪ 源码守卫: 旧 bug 模式不得复活; 全日期匹配/读回标记必须在
    src = pathlib.Path(gj.__file__).read_text(encoding="utf-8")
    assert '"昨日" if' not in src, "旧「昨日」误判逻辑不得复活"
    assert "/*GJ:cell*/" in src and "/*GJ:inputs*/" in src and "/*GJ:navbtn*/" in src, "翻月/全日期/读回标记缺失"
    assert "td[title='" in src, "必须按中文全日期 title 精确匹配"
    print("⓪ 源码守卫: 无「昨日」误判 / 全日期匹配+读回校验在场 ✓")

    # ① 单日 09-24: 翻月→点格→(视图跳回)→再翻月→再点同格→双框=09/24
    ad, pg, st = make()
    ad._set_gj_dates(pg, "2026-09-24", "2026-09-24")
    assert st["inputs"] == ["2026/09/24", "2026/09/24"], st["inputs"]
    assert st["cell_clicks"] == ["2026年9月24日", "2026年9月24日"], st["cell_clicks"]
    assert any("日期已选" in m for _, m in ad.sink.logs), ad.sink.logs
    assert st["nav_clicks"], "应先翻月到 9 月"
    assert not st["stray_clicks"], f"不得点空: {st['stray_clicks']}"
    print("① 单日: 翻月→两击同格→双框读回 09/24 ✓")

    # ② 跨月区间 09-24 ~ 10-06: 起点在 9 月、终点在 10 月
    ad, pg, st = make()
    ad._set_gj_dates(pg, "2026-09-24", "2026-10-06")
    assert st["inputs"] == ["2026/09/24", "2026/10/06"], st["inputs"]
    assert st["cell_clicks"][0] == "2026年9月24日" and "2026年10月6日" in st["cell_clicks"], st["cell_clicks"]
    print("② 跨月区间: 起点/终点各自命中正确月份 ✓")

    # ③ 点击无效(面板半状态) → 重试一轮 → 仍不符 → 抛错(绝不静默继续)
    ad, pg, st = make(mode="noop")
    raised = ""
    try:
        ad._set_gj_dates(pg, "2026-09-24", "2026-09-24")
    except RuntimeError as e:
        raised = str(e)
    assert "日期未生效" in raised, f"应抛「日期未生效」, 实际: {raised!r} / logs={ad.sink.logs}"
    assert any("自动重试" in m for _, m in ad.sink.logs), ad.sink.logs
    print("③ 读回不符: 重试后仍不符 → 抛错(不写错数据) ✓")

    # ④ 面板打不开 → 立即抛错
    ad, pg, st = make(mode="noopen")
    raised = ""
    try:
        ad._set_gj_dates(pg, "2026-09-24", "2026-09-24")
    except RuntimeError as e:
        raised = str(e)
    assert "面板未弹出" in raised, f"应抛「面板未弹出」, 实际: {raised!r}"
    print("④ 面板未弹出: 明确抛错 ✓")

    # ⑤ 输入框一直不可见(页面未就绪) → 明确抛错(不闷等 30s)
    ad, pg, st = make(mode="noinput")
    raised = ""
    try:
        ad._set_gj_dates(pg, "2026-09-24", "2026-09-24")
    except RuntimeError as e:
        raised = str(e)
    assert "输入框未就绪" in raised, f"应抛「输入框未就绪」, 实际: {raised!r}"
    print("⑤ 输入框不可见: 明确抛错 ✓")

    print("\n✅ 管家日期选择: 翻月 / 全日期匹配 / 两击 / 读回校验 全绿")


if __name__ == "__main__":
    main()
