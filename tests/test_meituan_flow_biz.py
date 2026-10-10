# -*- coding: utf-8 -*-
"""流量页「业务」下拉(全部/外卖/拼好饭): 硬证据验收 + 切不到就不写。

真机背景(2026-10-08): 该下拉默认值是「全部」(= 外卖+拼好饭), 老实现假设"页面默认就是外卖口径"
→ 写进主站 TOP 列的是全部口径(偏高) ✗, 拼好饭 TOP 列一直空 ✗;
且老 _set_flow_biz 用位置启发式 + mouse.click + 选项要求 children.length===0(真机 li 内含 span → 恒 false),
永远切不动 → 这里同时守住"坏模式不许回来"。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan as mt                          # noqa: E402


class FakeLoc:
    def __init__(self, page):
        self.page, self.first = page, self

    def click(self, timeout=None):
        self.page.clicks += 1


class FakePage:
    """accepts=False 模拟"点了选项但页面没吃下"(值不变)。"""

    def __init__(self, biz="全部", accepts=True):
        self.biz, self.accepts, self.clicks, self.url = biz, accepts, 0, "https://x/flowrate"

    def evaluate(self, js, arg=None):
        if "customRoo-input" in js and "data-hermes-biz" in js:      # 给业务下拉打标记
            return 1
        if "customRoo-selector-option-default" in js:                # 点选项
            if self.accepts:
                self.biz = arg
            return 1
        if "el.value || ''" in js:                                   # 读当前值
            return self.biz
        return None

    def locator(self, sel):
        return FakeLoc(self)


def mk(page):
    ad = mt.MeituanAdapter.__new__(mt.MeituanAdapter)
    logs = []
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append((level, m))})()
    return ad, logs


def main():
    # ① 读当前业务值
    p = FakePage("全部")
    ad, _ = mk(p)
    assert ad._flow_biz(p) == "全部", ad._flow_biz(p)
    print("✅ 读到当前业务 = 全部")

    # ② 已是目标值 → 幂等, 不点
    assert ad._set_flow_biz(p, "全部") is True and p.clicks == 0, p.clicks
    print("✅ 已是目标业务: 零点击直接返回")

    # ③ 切到「外卖」成功 → 值真的变了 + 日志"已生效"
    p = FakePage("全部", accepts=True)
    ad, logs = mk(p)
    assert ad._set_flow_biz(p, "外卖") is True, logs
    assert p.biz == "外卖", p.biz
    assert any("已生效" in m for _, m in logs), logs
    print("✅ 切「外卖」: 值已变更 + 判「已生效」")

    # ④ 页面没吃下 → 判「未确认」(调用方据此留空, 不拿"全部"冒充外卖)
    p = FakePage("全部", accepts=False)
    ad, logs = mk(p)
    assert ad._set_flow_biz(p, "拼好饭") is False, logs
    assert any("未确认" in m for _, m in logs), logs
    print("✅ 页面没吃下: 判「未确认」→ 调用方留空")

    # ⑤ 调用方: capture_circle 必须分别切 外卖 / 拼好饭
    src = inspect.getsource(mt.MeituanAdapter.capture_circle)
    assert '_set_flow_biz(page, "外卖")' in src, "主站 TOP 必须先切「外卖」(不能拿全部口径冒充)"
    assert '_set_flow_biz(page, "拼好饭")' in src, "拼好饭 TOP 必须切「拼好饭」"
    assert 'result["bao"] = _bao' in src, "拼好饭商圈值要落到 bao 槽(映射层已支持)"
    print("✅ capture_circle: 分别切「外卖」→ 主站 / 「拼好饭」→ 拼好饭")

    # ⑥ 守卫: 旧的坏模式不许回来
    biz_src = inspect.getsource(mt.MeituanAdapter._set_flow_biz)
    assert "mouse.click" not in biz_src, "业务切换不许再用坐标点击(多层结构必落错)"
    assert "children.length === 0" not in biz_src, "不许再用 children.length===0 筛选项(真机恒 false)"
    print("✅ 守卫: 无坐标点击 / 无 children.length===0 假阴性")

    print("✅ 流量页业务切换(外卖/拼好饭)单测全部通过")


if __name__ == "__main__":
    main()
