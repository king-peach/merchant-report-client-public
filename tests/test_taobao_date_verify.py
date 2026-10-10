# -*- coding: utf-8 -*-
"""淘宝下载: 日期没设上 / 下回来的日期不对 —— 必须当场中止并说清(2026-09-30 真机)。

现场: 请求 2026-09-28, 日志里「📅 下载区间: 2026-09-28 ~ 2026-09-28」也打了, 但平台生成的是
**09-29** 的报表(下载页那个 React 日期控件没接受我们直接写的 value) → 按"名字区间+内容日期"
筛选时两份都被排除 → 报「缺全部/爆品团报表（已有: 无）」, 用户以为"程序没找到文件"。

三条修复, 各一条测试:
  ① _set_date_range 读回校验 + 键盘重试 + 不生效就中止(美团早有这道闸, 淘宝补齐);
  ② 规范名改用**平台声明区间**, 不能用请求区间给别日期的报表命名;
  ③ 「缺报表」报错要带上下载清单(名字区间/内容日期/是否含请求日期)。
"""
import os
import pathlib
import sys
import tempfile

import openpyxl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report import fill                                                       # noqa: E402
from merchant_report.platforms import taobao as tb                                     # noqa: E402


# ---------------- ① 平台原名里的区间 ----------------
def test_platform_range_in_name():
    assert fill.platform_range_in_name(
        "门店下载_20260929至20260929_全部门店_1111111111_20260930164137707.xlsx") == \
        ("2026-09-29", "2026-09-29")
    assert fill.platform_range_in_name(
        "门店下载_20260924至20260927_全部门店_1111111111_20260928150037365.xlsx") == \
        ("2026-09-24", "2026-09-27")
    assert fill.platform_range_in_name("随便一个文件.xlsx") is None
    print("✅ 能读出平台原名里实际提交的区间")


# ---------------- ② 下载清单自证 ----------------
def _tpl_xlsx(path, header, date, name):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(header)
    ws.append([str(date), name, 1001, 1, 2, 3])
    wb.save(path)
    return path


def test_downloaded_but_unusable_hint():
    with tempfile.TemporaryDirectory() as d:
        _tpl_xlsx(f"{d}/门店下载_20260929至20260929_全部门店_1111111111_20260930164137707.xlsx",
                  ["日期", "门店名称", "门店编号", "满减活动订单数", "营业额"], "20260929", "某店")
        items = fill.downloaded_but_unusable(d, "2026-09-28", "2026-09-28")
        assert len(items) == 1, items
        it = items[0]
        assert it["named_range"] == "2026-09-29~2026-09-29" and it["covers"] is False, it
        assert it["content_dates"] == "2026-09-29", it
        hint = fill.unusable_report_hint(items)
        assert "名字区间 2026-09-29~2026-09-29" in hint and "内容不含请求日期" in hint, hint
        print(f"✅ 报错清单说清了「下到了但不是这天」: {hint[:70]}…")
        assert "没有任何可用大小" in fill.unusable_report_hint([])
        print("✅ 空目录也有明确说法")


# ---------------- ③ 规范名用平台区间 ----------------
def test_rename_uses_platform_range():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(_tpl_xlsx(
            f"{d}/门店下载_20260929至20260929_全部门店_1111111111_20260930164137707.xlsx",
            ["日期", "门店名称", "门店编号", "满减活动订单数", "营业额"], "20260929", "某店"))
        logs = []
        ad = tb.TaobaoAdapter.__new__(tb.TaobaoAdapter)
        ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append(m)})()
        ad._dl_range = ("2026-09-28", "2026-09-28")          # 本次请求 09-28
        new = ad._rename_canonical(p)
        assert new.name.startswith("淘宝_全部_20260929_20260929_"), new.name
        assert "20260928" not in new.name, new.name
        assert any("不一致" in m for m in logs), logs
        print(f"✅ 按平台真实区间命名并告警: {new.name[:52]}…")


# ---------------- ① 日期读回校验 ----------------
class FakeKeyboard:
    def __init__(self, logs, on_type=None):
        self.logs = logs
        self.on_type = on_type

    def press(self, key):
        self.logs.append(("press", key))

    def type(self, text, delay=0):
        self.logs.append(("type", text))
        if self.on_type:
            self.on_type(text)


class FakeMouse:
    def __init__(self, logs):
        self.logs = logs

    def click(self, x, y):
        self.logs.append(("click", round(x), round(y)))


class FakePage:
    def __init__(self, logs, on_type=None):
        self.logs = logs
        self.mouse = FakeMouse(logs)
        self.keyboard = FakeKeyboard(logs, on_type)


class FakeFrame:
    """accept_js: 直接写 value 是否生效(模拟 React 控件认不认); accept_type: 键盘输入是否生效。"""

    def __init__(self, values, accept_js=False, accept_type=False):
        self.values = list(values)
        self.accept_js = accept_js
        self.accept_type = accept_type

    def evaluate(self, js, args=None):
        if args is None:                      # 读输入框
            return [{"x": 10 + i * 100, "y": 20, "v": v} for i, v in enumerate(self.values)]
        if self.accept_js:
            self.values = list(args)
            if len(args) > 1:
                self.values = list(args)
        return 2


def _adapter(logs, on_type=None, values=("2026-09-26", "2026-09-26")):
    ad = tb.TaobaoAdapter.__new__(tb.TaobaoAdapter)
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append(m)})()
    ad.page = FakePage(logs, on_type=on_type)
    return ad


def test_date_range_ok_when_js_works():
    logs = []
    ad = _adapter(logs)
    f = FakeFrame(["2026-09-26", "2026-09-26"], accept_js=True)
    assert ad._set_date_range(f, "2026-09-28", "2026-09-28") == 2
    assert any("下载区间已确认" in m for m in logs), logs
    print("✅ 日期直接写入生效: 读回确认后才继续")


def test_date_range_falls_back_to_keyboard():
    logs = []
    typed = []

    def on_type(t):
        typed.append(t)
        f.values[0 if len(typed) == 1 else 1] = t

    ad = _adapter(logs, on_type=on_type)
    f = FakeFrame(["2026-09-26", "2026-09-26"], accept_js=False, accept_type=True)
    assert ad._set_date_range(f, "2026-09-28", "2026-09-28") == 2
    assert typed == ["2026-09-28", "2026-09-28"], typed
    assert any("下载区间已确认" in m for m in logs), logs
    print("✅ 直接写不生效 → 键盘输入兜住, 仍确认后继续")


def test_date_range_aborts_when_nothing_works():
    logs = []
    ad = _adapter(logs)
    f = FakeFrame(["2026-09-26", "2026-09-26"], accept_js=False, accept_type=False)
    try:
        ad._set_date_range(f, "2026-09-28", "2026-09-28")
        raise AssertionError("日期没设上却没有中止 —— 会用错区间的报表填表")
    except RuntimeError as e:
        msg = str(e)
    assert "期望 2026-09-28~2026-09-28" in msg and "2026-09-26" in msg, msg
    print(f"✅ 日期设不上 → 当场中止: {msg[:60]}…")


def main():
    for fn in (test_platform_range_in_name, test_downloaded_but_unusable_hint,
               test_rename_uses_platform_range, test_date_range_ok_when_js_works,
               test_date_range_falls_back_to_keyboard, test_date_range_aborts_when_nothing_works):
        fn()
    print("✅ 淘宝下载日期校验(不生效即中止)+ 下载清单自证 单测全部通过")


if __name__ == "__main__":
    main()
