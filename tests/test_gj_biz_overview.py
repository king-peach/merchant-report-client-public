# -*- coding: utf-8 -*-
"""美团管家「营业概览」打开逻辑单测(fake page, 不启动浏览器)。

背景(2026-09-28 真机): 31 店连跑约 4 家报「报表中心未找到「营业概览」入口」——
侧边栏现在只有可展开父级「营业报表」, 菜单异步渲染时机不稳, 重试点击救不回来。
改为**优先直连路由** #/rms-report/business-report, 直连不成才回退点菜单。
另: 诊断包里的页面文本被「品牌商-示例店长 00001」刷屏, 等于没信息 → _text_brief 折叠重复。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan_gj as gj                        # noqa: E402


class FakeMouse:
    def __init__(self, log):
        self.log = log

    def click(self, x, y):
        self.log.append(("mouse_click", round(x), round(y)))


class FakePage:
    """ready_after: 第 N 次就绪检查起才返回 True(模拟渲染延迟); 直连失败用 None 表示永不就绪。"""

    def __init__(self, ready_after=1, biz_url=None, have_menu=True):
        self.url = "https://pos.meituan.com/web/report/main#/rms-report/home"
        self.biz_url = biz_url or gj.GJ_BIZ_OVERVIEW
        self.ready_after = ready_after
        self.have_menu = have_menu
        self.log = []
        self.mouse = FakeMouse(self.log)
        self._n = 0

    def goto(self, url, wait_until=None):
        self.log.append(("goto", url))
        self.url = url

    def evaluate(self, js, arg=None):
        if "请选择日期" in js or "ant-calendar-picker" in js:
            self._n += 1
            if self.ready_after is None:
                return False
            return self._n >= self.ready_after
        if "textContent" in js:                      # _visible / 点菜单的定位
            return {"x": 100, "y": 50, "w": 60} if self.have_menu else None
        if "document.body.innerText" in js:
            return ("品牌商-示例店长 00001 " * 8) + " 营业概览 营业收入 100 营业额 200"
        raise AssertionError(f"未预期的 JS: {js[:60]}")


def mk(page):
    ad = gj.MeituanGjAdapter.__new__(gj.MeituanGjAdapter)     # 不走 __init__(不需要浏览器)
    ad.sink = type("S", (), {"log": lambda self, m, level="info": page.log.append(("log", m))})()
    return ad


def main():
    # ① 直连成功: 只 goto 一次, 完全不点菜单
    p = FakePage(ready_after=1)
    ad = mk(p)
    assert ad._open_biz_overview(p) is p
    assert ("goto", gj.GJ_BIZ_OVERVIEW) in p.log, p.log
    assert not any(x[0] == "mouse_click" for x in p.log), f"直连成功不该点菜单: {p.log}"
    print("✅ 直连成功: 一次 goto 打开营业概览, 零菜单点击")

    # ② 已经在营业概览且就绪 → 不再 goto(幂等)
    p = FakePage(ready_after=1, biz_url=None)
    p.url = "https://pos.meituan.com/web/report/main#/rms-report/business-report"
    ad = mk(p)
    assert ad._open_biz_overview(p) is p
    assert not any(x[0] == "goto" for x in p.log), f"已就绪不该再导航: {p.log}"
    print("✅ 幂等: 已在营业概览页就绪 → 零导航")

    # ③ 直连有延迟(第 3 次检查才就绪) → 仍然成功, 不需要回退
    p = FakePage(ready_after=3)
    ad = mk(p)
    assert ad._open_biz_overview(p) is p
    assert not any(x[0] == "mouse_click" for x in p.log), p.log
    print("✅ 渲染慢: 直连等就绪(条件等待), 不用点菜单也不误判失败")

    # ④ 直连始终不就绪 → 回退点菜单(报告中心 + 营业概览), 仍能打开
    p = FakePage(ready_after=99)

    def eval_ready(js, arg=None):
        # 只有**点过菜单**之后页面才就绪 → 直连必然失败, 强制走回退路径
        if "请选择日期" in js or "ant-calendar-picker" in js:
            return any(x[0] == "mouse_click" for x in p.log)
        return FakePage.evaluate(p, js, arg)
    p.evaluate = eval_ready
    ad = mk(p)
    assert ad._open_biz_overview(p) is p
    assert any(x[0] == "mouse_click" for x in p.log), f"应回退到点菜单: {p.log}"
    assert any("回退点菜单" in str(x[1]) for x in p.log if x[0] == "log"), p.log
    print("✅ 兜底: 直连不就绪 → 回退点菜单并成功打开")

    # ⑤ 两条路都失败 → 明确报错(而不是继续往下跑读错数据)
    p = FakePage(ready_after=None, have_menu=False)
    ad = mk(p)
    try:
        ad._open_biz_overview(p)
        raise AssertionError("应当抛错")
    except RuntimeError as e:
        assert "营业概览" in str(e), e
        assert "×" in str(e), f"报错文本应折叠重复片段: {e}"
    print("✅ 都失败: 明确抛错, 且页面文本已折叠重复(诊断包不再被刷屏文本灌满)")

    # ⑥ _biz_ready: 缺日期框 → 不就绪(防"URL 变了但内容没渲染"被当成成功)
    class NoInput(FakePage):
        def evaluate(self, js, arg=None):
            if "请选择日期" in js or "ant-calendar-picker" in js:
                return False
            return FakePage.evaluate(self, js, arg)
    ad = mk(NoInput())
    assert ad._biz_ready(NoInput()) is False
    print("✅ 就绪判据: URL+日期框+收入区块 三者齐全才算就绪")

    # ⑦ _text_brief 折叠重复
    ad = mk(FakePage())
    t = ad._text_brief(FakePage())
    assert "×8" in t and len(t) < 200, t
    print(f"✅ 文本摘要折叠重复: {t[:80]}…")

    print("✅ 管家「营业概览」打开逻辑单测全部通过")


if __name__ == "__main__":
    main()
