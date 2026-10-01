# -*- coding: utf-8 -*-
"""P0 批量韧性单测: 用假页面驱动真实逐店循环, 覆盖 重试/跳过/结束/无响应/全灭 五条路径"""
import sys
import types

sys.path.insert(0, "/Users/wangtao/workspace/merchant-report-client/kernel")

import merchant_report.platforms.meituan_gj as gjmod
from merchant_report.platforms.meituan_gj import MeituanGjAdapter

gjmod.time = types.SimpleNamespace(sleep=lambda s: None)   # 去掉循环里的等待

STORES = [{"name": f"门店{i}", "mid": str(1000 + i)} for i in range(1, 5)]


class FakeSink:
    def __init__(self):
        self.logs, self.needs = [], []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))

    def need_human(self, action, message, platform=None, timeout_s=300, **kw):
        self.needs.append({"message": message, **kw})

    def step_ok(self, *a, **k):
        pass

    def step_fail(self, *a, **k):
        pass


class FakeHuman:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def wait(self, obj):
        self.calls += 1
        if not self.answers:
            return None
        a = self.answers.pop(0)
        return {"choice": a} if a else None


class FakePage:
    def __init__(self):
        self.url = "https://pos.meituan.com/web/rms-account#/selectorg"
        self.gotos = []

    def is_closed(self):
        return False

    def goto(self, url, wait_until=None):
        self.gotos.append(url)

    def evaluate(self, js, arg=None):
        if "innerText" in js:
            return "请选择要登录 集团/门店"
        if "商户号" in js:
            return STORES
        return None


def make(answers, always_fail, first_fail=None):
    sink, human = FakeSink(), FakeHuman(answers)
    ad = MeituanGjAdapter(None, sink, human)
    ad.page = FakePage()
    ad._is_logged_in = lambda p: True
    ad._select_store = lambda page, name: True
    calls = {}

    def fake_scrape(page, name, df, dt):
        calls[name] = calls.get(name, 0) + 1
        if name in always_fail or (first_fail and name in first_fail and calls[name] == 1):
            ad._last_err = "模拟：营业概览页读不到渠道数据（页面结构变化）"
            return None
        return {"店内销售": {"营业收入": 1.0, "营业额": 2.0, "订单量": 3.0}}

    ad._scrape_one = fake_scrape
    return ad, sink, human, calls


def run_case(title, answers, always_fail=(), first_fail=()):
    ad, sink, human, calls = make(answers, set(always_fail), set(first_fail))
    err = None
    try:
        ad.download_reports("/tmp/gj_batch_test", date_from="2026-09-20", date_to="2026-09-20")
    except Exception as e:
        err = str(e)[:60]
    return ad, sink, human, calls, err


print("=== A. 单店首次失败 → 用户点重试 → 成功 ===")
ad, sink, human, calls, err = run_case("A", ["直接重试"], first_fail={"门店2"})
assert not err, err
assert len(ad.scraped) == 4, list(ad.scraped)
assert ad.failed == [], ad.failed
assert calls["门店2"] == 2 and len(sink.needs) == 1
print(f"  成功 {len(ad.scraped)}/4 店, 门店2 尝试 {calls['门店2']} 次, 弹面板 {len(sink.needs)} 次 ✓")

print("=== B. 某店持续失败 → 跳过继续下一家 ===")
ad, sink, human, calls, err = run_case("B", ["跳过这家店，继续下一家"], always_fail={"门店2"})
assert not err, err
assert set(ad.scraped) == {"门店1", "门店3", "门店4"}, list(ad.scraped)
assert len(ad.failed) == 1 and ad.failed[0]["name"] == "门店2", ad.failed
assert "门店3" in ad.scraped and "门店4" in ad.scraped, "跳过后必须继续跑后续门店"
print(f"  成功 {sorted(ad.scraped)}, 失败清单={ad.failed} ✓ 后续门店未被拖累")

print("=== C. 某店失败 → 用户选择结束任务 ===")
ad, sink, human, calls, err = run_case("C", ["结束任务"], always_fail={"门店2"})
assert not err, err
assert set(ad.scraped) == {"门店1"}, list(ad.scraped)
assert ad.failed and "结束任务" in ad.failed[-1]["reason"], ad.failed
assert ad.failed[-1]["name"] == "门店2"
print(f"  已完成门店保留 {sorted(ad.scraped)}, 末尾记录={ad.failed[-1]} ✓ 不再跑门店3/4")

print("=== D. 无响应(无人值守/超时) → 保守跳过继续 ===")
ad, sink, human, calls, err = run_case("D", [None], always_fail={"门店2"})
assert not err, err
assert set(ad.scraped) == {"门店1", "门店3", "门店4"}, list(ad.scraped)
assert len(ad.failed) == 1
print(f"  成功 {sorted(ad.scraped)}, 失败 {len(ad.failed)} 家 ✓ 不中断整批")

print("=== E. 全部门店失败 → 明确报错(不谎报成功) ===")
ad, sink, human, calls, err = run_case("E", ["跳过这家店，继续下一家"] * 4,
                                       always_fail={"门店1", "门店2", "门店3", "门店4"})
assert err and "无任何门店抓到数据" in err, err
print(f"  抛出: {err} ✓")

print("=== F. capture_stats 结构(供编排层汇总失败清单) ===")
ad, sink, human, calls, err = run_case("F", ["跳过这家店，继续下一家"], always_fail={"门店3"})
st = ad.capture_stats()
assert st["platform"] == "meituan_gj" and len(st["stores"]) == 3 and len(st["failed"]) == 1
print(f"  stores={len(st['stores'])} failed={st['failed']} ✓")

print("\n✅ P0 批量韧性单测全部通过（5 条路径）")
