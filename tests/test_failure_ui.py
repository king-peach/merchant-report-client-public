# -*- coding: utf-8 -*-
"""P0 卡点面板离线单测: 分类解释 / 动态选项 / 各分支动作 / 兜底行为"""
import sys

sys.path.insert(0, "/Users/wangtao/workspace/merchant-report-client/kernel")

from merchant_report import failure_ui
from merchant_report.diagnose import classify, explain


class FakeSink:
    def __init__(self):
        self.events = []
        self.sent = []

    def need_human(self, action, message, platform=None, timeout_s=300, **kw):
        self.events.append({"action": action, "message": message, "platform": platform,
                            "timeout_s": timeout_s, **kw})

    def log(self, msg, level="info"):
        self.sent.append((level, msg))


class FakeHuman:
    def __init__(self, resp):
        self.resp = resp

    def wait(self, obj):
        return self.resp


def run(choice, **kw):
    sink, human = FakeSink(), FakeHuman({"choice": choice} if choice is not None else None)
    res = failure_ui.ask_failure(sink, human, platform="美团管家", store="金盆岭理工大学店",
                                 date="2026-09-20", step="第 3/16 家门店 · 营业概览读数",
                                 error_text=kw.pop("err", "页面元素未找到"),
                                 allow_skip=kw.pop("allow_skip", True),
                                 allow_relogin=kw.pop("allow_relogin", False),
                                 attempt=kw.pop("attempt", 0), max_retry=kw.pop("max_retry", 2),
                                 page=None)
    return res, sink


print("=== 1. 分类 + 人话解释 ===")
cases = [
    ("页面元素未找到: 未匹配到 营业收入", "E_STRUCTURAL"),
    ("登录失效 请重新登录 login", "E_AUTH"),
    ("Timeout 30000ms exceeded 网络超时", "E_TRANSIENT"),
    ("奇怪的错误 xyz", "E_UNKNOWN"),
]
for text, want in cases:
    got = classify(text)
    ex = explain(got)
    assert got == want, (text, got, want)
    print(f"  {want}: {ex['title']} → {ex['choices']}")

print("=== 2. 各用户选择 → 动作 ===")
checks = [
    ("直接重试", "retry"),
    ("跳过这家店，继续下一家", "skip"),
    ("结束任务", "abort"),
    ("发送诊断包", "diagnose"),
    ("重新登录", "relogin"),
]
for choice, want in checks:
    res, _ = run(choice, allow_relogin=True)
    assert res["action"] == want, (choice, res)
    print(f"  「{choice}」→ {res['action']} ✓")

print("=== 3. 兜底: 无响应(CLI/超时) ===")
res, sink = run(None)
assert res["action"] == "skip", res
print(f"  allow_skip=True 无响应 → {res['action']} ✓ ({sink.sent[-1][1][:40]}…)")
res, _ = run(None, allow_skip=False)
assert res["action"] == "abort", res
print(f"  allow_skip=False 无响应 → {res['action']} ✓")

print("=== 4. 选项裁剪(按场景收紧) ===")
_, sink = run("直接重试", allow_skip=False, allow_relogin=False)
ch = sink.events[0]["choices"]
assert not any("跳过" in c for c in ch) and not any("重新登录" in c for c in ch), ch
print(f"  顶层步骤(不可跳过/不可重登): {ch} ✓")
_, sink = run("直接重试", allow_relogin=True, attempt=2, max_retry=2)
ch = sink.events[0]["choices"]
assert not any("重试" in c for c in ch), ch
print(f"  已达重试上限(attempt=2): {ch} ✓")

print("=== 5. 事件字段(客户端渲染所需) ===")
_, sink = run("直接重试", allow_relogin=True, err="未匹配到元素 no such element")
ev = sink.events[0]
ctx = ev["context"]
required = ("platform", "store", "date", "step", "error_code", "title", "hint", "error", "shot", "attempt")
missing = [k for k in required if k not in ctx]
assert not missing, missing
assert ev["timeout_s"] == failure_ui.DEFAULT_TIMEOUT_S
assert ev["action"] == "confirm" and ctx["error_code"] == "E_STRUCTURAL"
print(f"  字段齐全 ✓ error_code={ctx['error_code']} attempt={ctx['attempt']} platform={ctx['platform']}")
print(f"  消息: {ev['message']}")

print("=== 6. AI 诊断附加信息透传 ===")
sink, human = FakeSink(), FakeHuman({"choice": "直接重试"})
failure_ui.ask_failure(sink, human, platform="美团", store="x", step="下载报表",
                       error_text="未找到元素", page=None,
                       extra={"ai_diagnosis": "页面已改版", "ai_suggestion": "延长等待到 8s"})
ctx = sink.events[0]["context"]
assert ctx["ai_diagnosis"] == "页面已改版" and ctx["ai_suggestion"] == "延长等待到 8s"
print(f"  ✓ {ctx['ai_diagnosis']} / {ctx['ai_suggestion']}")

print("\n✅ P0 卡点面板离线单测全部通过")
