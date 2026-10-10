# -*- coding: utf-8 -*-
"""切店修复单测(2026-10-11): ① 全名判据 ② 点两次重试 ③ 前缀陷阱不误判
   v2 加固: ④ 点前即时复核 ⑤ 双读数稳定。

真机背景(探针 v3/v4/v5 + e2e, 切店失败 45s/切错店的根因):
  · 弹窗列表异步加载, 行出现在 DOM 时上面盖着 saas-ui-spin 遮罩 → 盲点被吞;
  · 列表重排会让同一坐标 0.3s 后变成另一家店的行(e2e 实测切错到 XXX-1楼);
  · 旧成功判据 `name[:6]` 把「长沙西服务区南区店」误判成「…北区店」(前 6 字相同);
  · 门店明细 id_map 加载了但 orchestrator 没传给 plan_matches → 编号锁定从未生效。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))

from merchant_report.platforms.meituan_gj import _gj_same_store          # noqa: E402


def test_same_store():
    # ① 全等
    assert _gj_same_store("金盆岭理工大学店", "金盆岭理工大学店")
    # ② 前缀陷阱: 前 6 字相同但非同一家 → 必须 False(旧判据这里 True, 这就是数据串店 bug)
    assert not _gj_same_store("长沙西服务区南区店", "长沙西服务区北区店")
    assert not _gj_same_store("长沙西服务区北区店", "长沙西服务区南区店")
    # ③ 装饰后缀容忍: 「门店名(装修中)」/全角空格 → 仍算同一家
    assert _gj_same_store("长沙高桥店（装修中）", "长沙高桥店")
    assert _gj_same_store("长沙高桥店", "长沙高桥店 ")
    assert _gj_same_store("长沙高桥店\u3000", "长沙高桥店")
    # ④ 短名互相包含不放行(防「XX店」⊂「XX店分店」误配)
    assert not _gj_same_store("高桥店", "高桥二分店")
    # ⑤ 空值
    assert not _gj_same_store("", "长沙高桥店")
    assert not _gj_same_store("长沙高桥店", "")
    print("✅ ① 全名判据: 前缀陷阱/装饰后缀/空值 全对")


class FakeMouse:
    def __init__(self):
        self.moves = []
        self.clicks = []

    def move(self, x, y):
        self.moves.append((x, y))

    def click(self, x, y):
        self.clicks.append((x, y))


class FakeModalPage:
    """模拟切店弹窗(新契约: _ROW_HIT_JS 返回 px/py/cx/cy/ok1/ok2; _VERIFY_JS 复核)。

    - row_reads: _ROW_HIT_JS 被调次数; first call 不 ok(被遮罩), 之后 ok。
    - verify_ok: _VERIFY_JS 是否放行(默认放行; 置 False 测"复核不过就不点")。
    - header_seq: 每次读表头弹出一个值。
    """

    def __init__(self, name, header_seq, first_read_blocked=True, verify_ok=True):
        self.name = name
        self.header_seq = list(header_seq)
        self.first_read_blocked = first_read_blocked
        self.verify_ok = verify_ok
        self.mouse = FakeMouse()
        self.row_reads = 0

    def evaluate(self, js, arg=None):
        if "elementFromPoint" in js and "closest" in js:      # _VERIFY_JS
            return {"ok": self.verify_ok}
        if "elementFromPoint" in js:                          # _ROW_HIT_JS
            self.row_reads += 1
            blocked = self.first_read_blocked and self.row_reads == 1
            return {"px": 100, "py": 200, "cx": 150, "cy": 200,
                    "txt": f"{self.name} 门店 123 456",
                    "ok1": not blocked, "ok2": not blocked}
        if "innerText" in js and "perspective-switch" in js:
            if self.header_seq:
                return self.header_seq.pop(0)
            return self.name
        if "document.querySelector" in js and "org-switch-modal" in js:
            return True
        return None


def _run_switch(ad, page):
    """直接驱动 _switch_store, 但绕过 _open_store_switch / locator 的键盘输入。"""
    ad._open_store_switch = lambda p: True

    class FakeLoc:
        class First:
            def click(self, timeout=None):
                pass

            def fill(self, v):
                pass

            def type(self, t, delay=None):
                pass

        first = First()

    page.locator = lambda sel: FakeLoc()
    return ad._switch_store(page, page.name)


def _adapter():
    import types
    from merchant_report.platforms.meituan_gj import MeituanGjAdapter
    ad = MeituanGjAdapter.__new__(MeituanGjAdapter)
    logs = []
    ad.sink = types.SimpleNamespace(log=lambda m, level="info": logs.append((level, m)))
    return ad, logs


def test_retry_double_click():
    """点一次被吞(8s 表头没变) → attempt1 预检查也是旧店 → 复核+再点第二次 → 成功。"""
    import merchant_report.platforms.meituan_gj as gjmod
    ad, logs = _adapter()

    # 读表头顺序: attempt0 等待 16 次旧店 → attempt1 预检查 1 次旧店 → 之后命中
    header_seq = ["旧店"] * 17 + ["测试目标店"] * 64
    page = FakeModalPage("测试目标店", header_seq)

    orig_sleep = gjmod.time.sleep
    gjmod.time.sleep = lambda s: None
    try:
        ok = _run_switch(ad, page)
    finally:
        gjmod.time.sleep = orig_sleep

    assert ok is True, logs
    assert page.row_reads >= 3, page.row_reads
    assert len(page.mouse.clicks) == 2, page.mouse.clicks
    assert any("第 2 次点击" in m for _, m in logs), logs
    print("✅ ② 点两次重试: 第 1 次被吞 → 第 2 次成功")


def test_confirm_full_name():
    """表头一直停在「相似但非目标」的店名上 → 判定失败, 不许当成切换成功。"""
    import merchant_report.platforms.meituan_gj as gjmod
    ad, logs = _adapter()

    header_seq = ["长沙西服务区南区店"] * 200
    page = FakeModalPage("长沙西服务区北区店", header_seq)

    orig_sleep = gjmod.time.sleep
    gjmod.time.sleep = lambda s: None
    try:
        ok = _run_switch(ad, page)
    finally:
        gjmod.time.sleep = orig_sleep

    assert ok is False, logs
    assert any("表头没变成目标店" in m and "南区店" in m for _, m in logs), logs
    print("✅ ③ 前缀陷阱: 南区店 ≠ 北区店, 正确判失败")


def test_verify_guard():
    """点前复核不通过(模拟列表重排, 坐标已不属于目标行) → 不发点击, 最终失败。"""
    import merchant_report.platforms.meituan_gj as gjmod
    ad, logs = _adapter()

    page = FakeModalPage("测试店", ["旧店"] * 200, verify_ok=False)

    orig_sleep = gjmod.time.sleep
    gjmod.time.sleep = lambda s: None
    try:
        ok = _run_switch(ad, page)
    finally:
        gjmod.time.sleep = orig_sleep

    assert ok is False, logs
    assert len(page.mouse.clicks) == 0, f"复核不过还敢点: {page.mouse.clicks}"
    print("✅ ④ 复核守门: 坐标未验证 → 一次都不点")


def test_stable_double_read():
    """第一读数 ok 但马上重排(两读数不稳定) → 不应直接点, 等稳定读数。"""
    import merchant_report.platforms.meituan_gj as gjmod
    ad, logs = _adapter()

    class JitterPage(FakeModalPage):
        def evaluate(self, js, arg=None):
            if "elementFromPoint" in js and "closest" in js:
                return {"ok": True}
            if "elementFromPoint" in js:
                self.row_reads += 1
                # 读数飘移: 前两读数不同(100→120), 之后稳定在 140
                jx = 100 if self.row_reads < 2 else (120 if self.row_reads < 3 else 140)
                return {"px": jx, "py": 200, "cx": jx + 50, "cy": 200,
                        "txt": f"{self.name} 门店 123 456", "ok1": True, "ok2": True}
            return super().evaluate(js, arg)

    page = JitterPage("测试店", ["旧店"] * 8 + ["测试店"] * 64)
    orig_sleep = gjmod.time.sleep
    gjmod.time.sleep = lambda s: None
    try:
        ok = _run_switch(ad, page)
    finally:
        gjmod.time.sleep = orig_sleep

    assert ok is True, logs
    # 点击坐标必须是稳定后的 140(py=200)
    assert page.mouse.clicks and page.mouse.clicks[0][0] == 140, page.mouse.clicks
    print("✅ ⑤ 双读数稳定: 抖动期间不点, 稳定坐标才点")


if __name__ == "__main__":
    test_same_store()
    test_retry_double_click()
    test_confirm_full_name()
    test_verify_guard()
    test_stable_double_read()
    print("\n✅ 切店修复单测全部通过(5 组)")
