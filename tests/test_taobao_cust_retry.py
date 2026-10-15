# -*- coding: utf-8 -*-
"""淘宝「顾客维度」切换加固单测(fake frame, 不启动浏览器) — 2026-10-15 任务 K2。

真机两轮复现: `⚠️ 外卖 顾客维度切「老客」失败 → 本次该维度留空`、`⚠️ 爆品团 …` ——
新客能切、老客必失败。根因是验收只 sleep(1.8) 定死等待、且只走一个选择器方案。
本次加固: ① 最多 2 次尝试(第 2 次前 Escape 清层); ② 验收改轮询(0.4s/次, 最长 6s);
③ 第 2 次换 B 方案(锚定「本店」漏斗区域内的 radio 组); ④ 最终失败落现场取证。

本单测锁死三件事(不依赖真浏览器, 手法照抄 test_taobao_day_pick.py 的 fake frame):
  ① 第 1 次验证失败、第 2 次成功 → 返回 True 且无 warn;
  ② 全部失败 → 返回 False + warn + 取证函数被调用(name 传对);
  ③ 取证函数抛异常 → 主流程不受影响(仍返回 False, 不炸)。
另钉: 既有两条日志文案与 A 方案匹配口径不许被"顺手改坏"。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import taobao as tb      # noqa: E402

tb.time.sleep = lambda *a, **k: None                     # 测试瞬时化(仅本进程)

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "kernel/merchant_report/platforms/taobao.py").read_text(encoding="utf-8")


class Log:
    def __init__(self):
        self.lines = []

    def log(self, msg, level="info"):
        self.lines.append((level, msg))

    def has(self, needle, level=None):
        return any(needle in m and (level is None or lv == level) for lv, m in self.lines)


class FakeKeyboard:
    def __init__(self, st):
        self.st = st

    def press(self, k):
        self.st["escapes"].append(k)


class FakeLocator:
    """点击后按当前方案生效与否决定是否真的切换成功。"""

    def __init__(self, st):
        self.st = st
        self.first = self

    def click(self, timeout=None):
        st = self.st
        scheme = st["scheme"]
        st["clicks"].append((scheme, timeout))
        if st["effective"].get(scheme):
            st["cust"] = st["pending"]
            st["checked"][scheme] = True


class FakeFrame:
    """state 关键位:
      cust       漏斗读回值(切换成功后变 pending)
      mark_a/b   A/B 方案打标数量(0 = 没找到 → 该次尝试直接失败)
      effective  {方案: 点击是否真的生效}
      scheme     最近一次打标用的是哪个方案
    """

    def __init__(self, st):
        self.st = st
        self.keyboard = FakeKeyboard(st)

    def evaluate(self, js, arg=None):
        st = self.st
        # 取证: 候选几何列表(含 Math.round)
        if "Math.round" in js:
            return []
        # _parse_funnel: 判别式必须是『流量趋势』—— 真 _parse_funnel 里同时含『流量转化』,
        # 若先判『流量转化』会把取证用的漏斗头部 JS 和它混起来(顺序要紧)
        if "流量趋势" in js:
            return {"cust": st["cust"], "ftype": "外卖", "nums": {}, "rates": {}}
        # 取证: 漏斗头部文本
        if "流量转化" in js:
            return "流量转化 曝光 进店 下单"
        # 取证: 页面上所有 radio label 的 [text, className](含 .map()
        if "ant-radio-button-wrapper" in js and ".map(" in js:
            return [["全部顾客", "ant-radio-button-wrapper"],
                    ["新客", "ant-radio-button-wrapper"],
                    ["老客", "ant-radio-button-wrapper"]]
        # 验收: 选中态 class
        if "ant-radio-button-wrapper" in js and "checked" in js:
            return bool(st["checked"].get(st["scheme"]))
        # 打标: B 方案(锚定「本店」漏斗区域 → 上溯 parentElement 找 ant-radio-group)
        # 判别用 parentElement: A 方案的 JS 注释里也含 "ant-radio-group" 字样, 不能当判别式
        if "data-hermes-cust" in js and "parentElement" in js:
            st["scheme"] = "B"
            return st["mark_b"]
        # 打标: A 方案(整页文本候选)
        if "data-hermes-cust" in js:
            st["scheme"] = "A"
            return st["mark_a"]
        raise AssertionError(f"未预期的 evaluate: {js[:60]}")

    def locator(self, sel):
        assert "data-hermes-cust" in sel, sel
        return FakeLocator(self.st)


def make(mark_a=1, mark_b=1, effective=None, cust="全部顾客"):
    st = {"cust": cust, "pending": "", "mark_a": mark_a, "mark_b": mark_b,
          "effective": effective or {}, "checked": {}, "scheme": None,
          "clicks": [], "escapes": []}
    fr = FakeFrame(st)
    ad = tb.TaobaoAdapter.__new__(tb.TaobaoAdapter)     # 不走 __init__(不需要浏览器)
    ad.sink = Log()
    ad.page = None
    return ad, fr, st


def main():
    # ① A 方案点击后验收一直不过(轮询到底失败) → 第 2 次(B 方案)成功 → True 且无 warn
    ad, f, st = make(mark_a=1, mark_b=1, effective={"A": False, "B": True})
    st["pending"] = "老客"
    assert ad._pick_cust(f, "老客") is True, "第 2 次尝试成功 → 必须返回 True"
    assert st["scheme"] == "B", f"第 2 次应走 B 方案, 实际 {st['scheme']}"
    assert ("Escape" in st["escapes"]), "两次尝试之间必须 Escape 清层"
    assert [s for s, _ in st["clicks"]] == ["A", "B"], st["clicks"]
    assert not any(lv == "warn" for lv, _ in ad.sink.lines), ad.sink.lines
    print("✅ 情形①: 第 1 次失败 → 清层 → B 方案成功 → True 且无 warn")

    # ①b A 方案一次过 → 只 1 次点击、不清层
    ad, f, st = make(mark_a=1, effective={"A": True})
    st["pending"] = "新客"
    assert ad._pick_cust(f, "新客") is True
    assert [s for s, _ in st["clicks"]] == ["A"], st["clicks"]
    assert st["escapes"] == [], "A 方案一次过不该清层"
    print("✅ 情形①b: A 方案一次过 → 单次点击、不清层")

    # ② 两次都失败 → False + warn + 取证被调用(name 传对)
    ad, f, st = make(mark_a=1, mark_b=1, effective={"A": False, "B": False})
    calls = []
    ad._cust_forensics = lambda fr, nm, before, after: calls.append((nm, before, after))
    assert ad._pick_cust(f, "老客") is False, "两次都不成必须返回 False"
    assert len(calls) == 1, f"取证应恰好被调用一次: {calls}"
    assert calls[0][0] == "老客", f"name 必须原样传给取证: {calls[0]}"
    assert calls[0][1] == "全部顾客" and calls[0][2] == "全部顾客", f"切换前后读回值: {calls[0]}"
    assert ad.sink.has("切顾客维度「老客」失败", level="warn"), ad.sink.lines
    assert ad.sink.has("切换前读回: '全部顾客', 尝试后读回: '全部顾客'"), ad.sink.lines
    print("✅ 情形②: 两次都失败 → False + warn + 取证被调用(name/前后读回值传对)")

    # ②b 连候选都找不到(mark=0) → 同样 False + 取证
    ad, f, st = make(mark_a=0, mark_b=0)
    calls = []
    ad._cust_forensics = lambda fr, nm, before, after: calls.append(nm)
    assert ad._pick_cust(f, "老客") is False
    assert calls == ["老客"], calls
    assert st["clicks"] == [], "没打标就不该点击"
    print("✅ 情形②b: 找不到候选 → 不点击, False + 取证")

    # ③ 取证函数抛异常 → 主流程不受影响(仍返回 False, 不炸)
    ad, f, st = make(mark_a=1, mark_b=1, effective={"A": False, "B": False})
    ad._cust_forensics = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("取证自身炸了"))
    assert ad._pick_cust(f, "老客") is False, "取证异常绝不能改变返回值/抛出"
    assert ad.sink.has("切顾客维度「老客」失败", level="warn"), ad.sink.lines
    print("✅ 情形③: 取证函数抛异常 → 主流程照常返回 False, 不炸")

    # ④ 幂等: 已经是目标维度 → 零交互直接 True
    ad, f, st = make(cust="老客")
    assert ad._pick_cust(f, "老客") is True
    assert st["clicks"] == [] and st["escapes"] == [], (st["clicks"], st["escapes"])
    print("✅ 情形④: 已是目标维度 → 零交互直接 True")

    # ⑤ 源码守卫: 加固要点在位, 且既有文案/口径没被改坏
    assert "_cust_verify_poll" in SRC and "poll=0.4" in SRC, "验收必须是 ~0.4s 轮询"
    assert "_mark_cust_by_store_group" in SRC, "B 方案(锚定本店漏斗区域)必须在位"
    assert "_cust_forensics" in SRC and "现场取证已保存" in SRC, "失败取证必须在位"
    assert "for attempt in (1, 2)" in SRC, "尝试次数上限必须是 2"
    # 既有两条日志文案一字不许改(上游 _capture_all_states 也在用)
    assert "{ftype} 顾客维度切「{cust}」失败 → 本次该维度留空" in SRC
    assert "顾客维度自校验未通过" in SRC
    # A 方案匹配口径不许被"顺手"放松
    assert "const want = (nm === '全部顾客') ? ['全部顾客'] : [nm];" in SRC, \
        "A 方案不许把「全部顾客」放松成模糊匹配"
    src_cust = inspect.getsource(tb.TaobaoAdapter._pick_cust)
    assert "time.sleep(1.8)" not in src_cust, "旧固定等待 time.sleep(1.8) 应已被轮询替代"
    assert "time.sleep(1.8)" not in SRC, "源码里不该再有固定 sleep(1.8) 调用(注释里提及不算)"
    print("✅ 情形⑤: 源码守卫(轮询/B方案/取证/上限2/文案与口径不变)全对")

    print("\n✅ 淘宝「顾客维度切换加固」单测全部通过")


if __name__ == "__main__":
    main()
