# -*- coding: utf-8 -*-
"""身份验证策略(L1)测试: 不自动拖滑块 + 立即交人工 + 人工接管后不刷新页面

背景(Windows 实测): 程序化拖滑块(贝塞尔合成轨迹)几乎必被判失败, 且 3 轮重试把风控评分推高,
导致用户之后手动滑动也过不去(页面以 error 码结束)。改为: 检测到验证控件立即交人工,
并明确提示扫码登录; 之后**只轮询登录态, 不再导航/刷新**。
"""
import sys
import types
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "kernel"))

import merchant_report.autologin as AL


class FakeSink:
    def __init__(self):
        self.events = []

    def log(self, msg, level="info"):
        self.events.append(("log", msg))

    def need_human(self, action, message, platform=None, timeout_s=300, **kw):
        self.events.append(("need_human", message))
        self.last_need = {"action": action, "message": message, "timeout_s": timeout_s}

    def step_fail(self, *a, **k):
        pass


class FakeMouse:
    """记录所有鼠标动作: 一旦被调用就说明发生了"自动拖滑块"。"""

    def __init__(self):
        self.moves = []
        self.downs = 0

    def move(self, x, y):
        self.moves.append((x, y))

    def down(self):
        self.downs += 1

    def up(self):
        pass


class FakePage:
    """带验证控件的页面。logged_in_after=N 次轮询后变为已登录。"""

    def __init__(self, slider=True, verify_text=True, logged_in_after=None):
        self.url = "https://melody.shop.ele.me/"
        self.gotos = []
        self.mouse = FakeMouse()
        self.slider = slider
        self.verify_text = verify_text
        self.poll_count = 0
        self.logged_in_after = logged_in_after

    def goto(self, url, **kw):
        self.gotos.append(url)

    def evaluate(self, js, arg=None):
        if "nc-lang-cnt" in js or "slider-track" in js:      # 滑块探测
            return {"hx": 100, "hy": 200, "trackW": 300} if self.slider else None
        if "滑动解锁" in js:                                  # 人工验证探测
            return self.verify_text
        if "getOwnPropertyDescriptor" in js:                  # 填账密
            return None
        if "登录" in js and "button" in js:                   # 点登录
            return None
        return None


class FakeAdapter:
    STATS_HOME = "https://melody.shop.ele.me/"

    def __init__(self, page, auto_slider=False):
        self.page = page
        self.ctx = types.SimpleNamespace(pages=[page])
        self.auto_slider = auto_slider
        self._poll = 0
        self._after = page.logged_in_after

    def _logged_in(self):
        self._poll += 1
        return bool(self._after and self._poll >= self._after)


def run(slider=True, verify_text=True, logged_in_after=2, auto_slider=False):
    page = FakePage(slider=slider, verify_text=verify_text, logged_in_after=logged_in_after)
    ad = FakeAdapter(page, auto_slider=auto_slider)
    sink = FakeSink()
    human = types.SimpleNamespace(wait=lambda obj: {"result": "done"})
    AL.time.sleep = lambda s: None            # 去掉等待
    ok = AL.auto_login(ad, "u", "p", sink, human, timeout_s=1)
    return ok, ad, page, sink


print("=== 1. 检测到滑块 → 立即交人工, 绝不自动拖动 ===")
ok, ad, page, sink = run(slider=True, logged_in_after=2)
assert ok is True, "人工完成验证后应判定登录成功"
assert page.mouse.downs == 0 and len(page.mouse.moves) == 0, \
    f"不应发生任何程序化拖动: downs={page.mouse.downs} moves={len(page.mouse.moves)}"
needs = [m for k, m in sink.events if k == "need_human"]
assert len(needs) == 1, needs
assert "扫码登录" in needs[0] and "不要刷新" in needs[0], needs[0]
print(f"  ✓ 鼠标动作 0 次, 单次人工提示: {needs[0][:44]}…")

print("=== 2. 人工接管后不再导航/刷新页面 ===")
assert len(page.gotos) == 1, f"人工接管后仍在导航(会把风控重新评分): {page.gotos}"
print(f"  ✓ goto 仅 1 次(接管前那次), 后续只轮询登录态")

print("=== 3. 只有验证文案(无滑块)同样交人工, 不误判为已登录 ===")
ok, ad, page, sink = run(slider=False, verify_text=True, logged_in_after=2)
assert ok is True and page.mouse.downs == 0
needs = [m for k, m in sink.events if k == "need_human"]
assert len(needs) == 1 and "身份验证" in needs[0]
print(f"  ✓ 短信/滚动验证类文案也走人工通道")

print("=== 4. 无需验证时直接登录成功(不打扰用户) ===")
ok, ad, page, sink = run(slider=False, verify_text=False, logged_in_after=2)
assert ok is True
assert not [m for k, m in sink.events if k == "need_human"], "无验证控件时不该弹人工提示"
print(f"  ✓ 直接成功, 无人工提示")

print("=== 5. 人工超时 → 明确失败并告警(不谎报成功) ===")
ok, ad, page, sink = run(slider=True, logged_in_after=None)
assert ok is False, "超时应返回 False"
warns = [m for k, m in sink.events if k == "log" and "超时" in m]
assert warns, sink.events
print(f"  ✓ 返回 False + 告警: {warns[0]}")

print("=== 6. auto_slider=True 时才启用旧的合成轨迹路径(保留开关) ===")
ok, ad, page, sink = run(slider=True, logged_in_after=3, auto_slider=True)
assert page.mouse.downs >= 1, "显式开启时应走旧拖动逻辑"
print(f"  ✓ 显式开启后拖动发生: downs={page.mouse.downs} moves={len(page.mouse.moves)}")

print("\n✅ 身份验证策略(L1)测试全部通过")
