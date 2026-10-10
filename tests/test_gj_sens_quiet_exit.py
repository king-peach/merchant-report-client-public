# -*- coding: utf-8 -*-
"""敏感操作抓取提速: "查询有响应 + 静默 ≥12s → 提前收工"(空店省 ~15s); 有值即时返回;
从未有响应 → 维持 40s 上限(情况不明不提前放弃)。

真机取证(2026-10-10 仪器探针, 坡子街/五里牌 09-26): 点击成功跳详情页;
查询响应 t≈10~15s 一次性到达 9 个空壳(无敏感键), 之后**再无任何响应** ——
旧逻辑傻等满 40s(实测 41.8s/店; 全零日 12 店/轮 ≈ 白等 8 分钟)。
"""
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan_gj as gj                           # noqa: E402
from merchant_report.platforms.meituan_gj import MeituanGjAdapter                # noqa: E402

SHELL = '{"data":{"summary":{}}}'
VALUE = json.dumps({"data": {"summary": {
    "sensitiveTimes": {"targetNum": 6}, "sensitiveAmt": {"targetNum": 131.4}}}})


class Clock:
    """假时钟: 驱动 _capture_sensitive 里的 time.time()/time.sleep()。"""

    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Resp:
    def __init__(self, body):
        self.url = "https://pos.meituan.com/web/api/v2/bi/runtime/query/graph?x=1"
        self._b = body

    def text(self):
        return self._b


class L:
    def count(self):
        return 1

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        return None


class F:
    def locator(self, sel):
        return L()


class FakePage:
    """script=[(装备于第N次wheel时, 响应体), ...]; 按脚本在 wheeling 时触发响应回调。"""

    def __init__(self, script):
        self.script = list(script)
        self.wheels = 0
        self.h = None
        self.frames = [F()]
        self.mouse = types.SimpleNamespace(wheel=self._wheel)

    def _wheel(self, x, y):
        self.wheels += 1
        for n, body in list(self.script):
            if self.wheels == n and self.h:
                self.h(Resp(body))
                self.script.remove((n, body))

    def on(self, ev, h):
        self.h = h

    def remove_listener(self, ev, h):
        pass

    def go_back(self, **kw):
        pass


def run_capture(page):
    ad = MeituanGjAdapter.__new__(MeituanGjAdapter)
    return ad._capture_sensitive(page, timeout_s=40)


def main():
    clk = Clock()
    orig = gj.time
    gj.time = clk
    try:
        # A) 空壳包到达后静默 → 提前收工(不是 40s)
        page = FakePage([(1, SHELL), (1, SHELL), (1, SHELL)])
        t0 = clk.t
        r = run_capture(page)
        el = clk.t - t0
        assert r is None, r
        assert 12 <= el <= 18, f"应静默12s后收工(实际 {el:.1f}s 假时钟)"
        print(f"✅ 空壳响应 → 静默 12s 提前收工: None @ {el:.1f}s(假时钟; 旧行为 40s)")

        # B) 带值响应 → 即时返回
        page = FakePage([(1, VALUE)])
        t0 = clk.t
        r = run_capture(page)
        assert r == {"times": 6.0, "amt": 131.4}, r
        assert clk.t - t0 < 5
        print(f"✅ 带值响应 → 即时返回 {r}")

        # C) 从未有响应 → 维持 40s 上限(不提前放弃)
        page = FakePage([])
        t0 = clk.t
        r = run_capture(page)
        el = clk.t - t0
        assert r is None and el >= 39, f"无响应必须走满上限(实际 {el:.1f}s)"
        print(f"✅ 从未有响应 → 走满 {el:.1f}s 上限后放弃(保守)")

        # D) 空壳后 3s 才到带值包(迟到的值) → 仍要抓住
        page = FakePage([(1, SHELL), (5, VALUE)])
        t0 = clk.t
        r = run_capture(page)
        assert r == {"times": 6.0, "amt": 131.4}, r
        assert clk.t - t0 < 10
        print("✅ 静默窗口内迟到的带值包 → 仍被抓住, 不误收工")
    finally:
        gj.time = orig

    # E) 契约: 关键结构不许丢
    import inspect
    src = inspect.getsource(MeituanGjAdapter._capture_sensitive)
    assert "quiet_s" in src and "state[\"seen\"]" in src, "静默收工判定必须保留"
    assert "timeout_s=40" in src, "上限参数默认 40s 不许改小"
    assert "sensitive-op" in src, "详情页链接选择器不许丢"
    print("✅ 契约: 静默收工 + 40s 上限 + 详情链接 守卫通过")

    print("✅ 敏感操作静默收工单测全部通过")


if __name__ == "__main__":
    main()
