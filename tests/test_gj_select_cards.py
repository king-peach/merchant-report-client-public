# -*- coding: utf-8 -*-
"""门店选择页卡片是**异步渲染**的: 等待出现再返回; 始终没有才返回空(由调用方报错)。

真机依据(2026-10-09): 「请选择要登录…」文本先出现、门店卡片列表后到 —— 直接读一次会
偶发拿到空列表 → 误报「无匹配门店」把任务拦死。E2E 实测踩到, 生产也会偶发。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan_gj as gj   # noqa: E402


class FakePage:
    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def evaluate(self, js):
        self.calls += 1
        return self.seq.pop(0) if self.seq else []


def main():
    fn = gj.MeituanGjAdapter._read_store_cards

    # ① 前两次空、第三次出现 → 要一直等到卡片
    fp = FakePage([[], [], [{"name": "长沙绿地之窗店", "mid": "87654321"}]])
    got = fn(None, fp, tries=6, wait=0)
    assert got and got[0]["name"] == "长沙绿地之窗店", got
    assert fp.calls == 3, fp.calls
    print("① 卡片晚出 → 等到第 3 次才返回 ✓")

    # ② 一直空 → 返回空列表(由调用方决定报错), 且尝试次数用满
    fp2 = FakePage([])
    got2 = fn(None, fp2, tries=4, wait=0)
    assert got2 == [] and fp2.calls == 4, (got2, fp2.calls)
    print("② 始终没有卡片 → 尝试 4 次后返回空(调用方报错) ✓")

    # ③ 首查即有 → 只查一次, 不白等
    fp3 = FakePage([[{"name": "x店", "mid": "1"}]])
    got3 = fn(None, fp3, tries=6, wait=0)
    assert len(got3) == 1 and fp3.calls == 1, (got3, fp3.calls)
    print("③ 首查即有 → 不白等 ✓")

    print("\n✅ 门店选择页卡片等待测试全绿")


if __name__ == "__main__":
    main()
