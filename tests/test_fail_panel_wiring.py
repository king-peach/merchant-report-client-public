# -*- coding: utf-8 -*-
"""P0 顶层失败面板接线测试: 假浏览器驱动真实 run_task, 验证 下载失败 → 面板事件 → 用户选择 生效"""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "kernel"))

import playwright.sync_api as psa

from merchant_report import orchestrator as O


class FakePage:
    url = "https://pos.meituan.com/web/rms-account"

    def is_closed(self):
        return False

    def evaluate(self, js, arg=None):
        return "美团管家 报表中心 营业概览"

    def screenshot(self, type="png", quality=None, timeout=None, path=None):
        return b"\xff\xd8\xff\xe0FAKEJPEG"


class FakeCtx:
    def __init__(self):
        self.pages = [FakePage()]
        self.closed = False

    def new_page(self):
        return FakePage()

    def close(self):
        self.closed = True


class FakePW:
    def start(self):
        return self

    def stop(self):
        pass


class FakeAdapter:
    """下载失败模式可配: 抛真实异常 / 软失败 / 前 N 次抛错后成功(用于验证浏览器重开自动恢复)"""

    download_calls = 0
    raise_mode = False      # True=抛真实异常(结构性) / False=软失败(返回 False)
    fail_first = 0          # >0: 前 N 次调用抛错, 之后返回 True(模拟"重开浏览器后成功")
    error_text = "未匹配到元素 selector=.report-download-btn（页面结构可能已变化）"
    launched = 0

    def __init__(self, ctx, sink, human, store_id="", keyword=""):
        self.sink, self.human, self.page = sink, human, FakePage()

    def ensure_login(self, username="", password="", **kw):
        return True

    def _dismiss_popups(self):
        pass

    def _discover_shop_id(self):
        pass

    def download_reports(self, ddir, date_from=None, date_to=None, **kw):
        FakeAdapter.download_calls += 1
        n = FakeAdapter.download_calls
        if FakeAdapter.fail_first and n <= FakeAdapter.fail_first:
            raise RuntimeError(FakeAdapter.error_text)
        if FakeAdapter.raise_mode:
            raise RuntimeError(FakeAdapter.error_text)
        if FakeAdapter.fail_first:
            return True          # 恢复之后成功
        return False

    def capture_stats(self, date_from=None, date_to=None):
        return {"platform": "meituan_gj", "stores": {}, "failed": []}


class FakeSink:
    def __init__(self):
        self.events, self.result_obj = [], None

    def _e(self, o):
        self.events.append(o)

    def log(self, msg, level="info"):
        self._e({"type": "log", "level": level, "msg": msg})

    def task_start(self, store, date, platforms):
        self._e({"type": "task_start", "store": store, "date": date, "platforms": platforms})

    def need_human(self, action, message, platform=None, timeout_s=300, **kw):
        self._e({"type": "need_human", "action": action, "message": message,
                 "platform": platform, "timeout_s": timeout_s, **kw})

    def step_start(self, sid, name, detail=None):
        self._e({"type": "step", "id": sid, "state": "running", "detail": detail})

    def step_ok(self, sid, name, detail=None, **kw):
        self._e({"type": "step", "id": sid, "state": "ok"})

    def step_fail(self, sid, name, detail=None, error_code=None, **kw):
        self._e({"type": "step", "id": sid, "state": "failed", "detail": detail})

    def step_skip(self, sid, name, detail=None):
        self._e({"type": "step", "id": sid, "state": "skipped"})

    def progress(self, step, pct):
        pass

    def result(self, ok, outputs=None, summary=None, error_code=None, message=None, retryable=False):
        self.result_obj = {"ok": ok, "error_code": error_code, "message": message}

    def llm_delta(self, scope, text):
        pass

    def llm_done(self, scope):
        pass


class FakeHuman:
    def __init__(self, answers):
        self.answers, self.objs = list(answers), []

    def wait(self, obj):
        self.objs.append(obj)
        a = self.answers.pop(0) if self.answers else None
        return {"choice": a} if a else None


def run(answers, raise_mode=False, fail_first=0, error_text=None):
    FakeAdapter.download_calls = 0
    FakeAdapter.raise_mode = raise_mode
    FakeAdapter.fail_first = fail_first
    FakeAdapter.launched = 0
    if error_text:
        FakeAdapter.error_text = error_text
    psa.sync_playwright = lambda: FakePW()
    O.sync_playwright = FakePW() if hasattr(O, "sync_playwright") else None
    def _launch(pw, prof):
        FakeAdapter.launched += 1
        return FakeCtx()

    O.launch_browser = _launch
    O.MeituanGjAdapter = FakeAdapter
    sink, human = FakeSink(), FakeHuman(answers)
    cfg = {"defaults": {}, "browser_profile": "/tmp/fake-profile"}
    store = {"name": "测试商家", "platform": "meituan_gj", "template": "/tmp/t.xlsx",
             "credentials": {"username": "u", "password": "p"}}
    O.run_task(cfg, store, date="2026-09-20", date_from="2026-09-20",
               sink=sink, human=human, llm_client=None)
    return sink, human


print("=== 1. 下载失败 → 面板事件字段完整 ===")
sink, human = run(["结束任务"])
panels = [e for e in sink.events if e["type"] == "need_human"]
assert panels, "未弹出卡点面板"
p = panels[0]
ctx = p.get("context") or {}
assert ctx.get("platform") == "美团管家", ctx.get("platform")
assert ctx.get("store") == "测试商家"
assert ctx.get("step") == "下载报表"
assert ctx.get("error_code") in ("E_UNKNOWN", "E_STRUCTURAL", "E_TRANSIENT", "E_AUTH"), ctx.get("error_code")
assert ctx.get("title") and ctx.get("hint")
assert ctx.get("shot", "").startswith("data:image/jpeg;base64,"), "缺少现场截图"
assert p.get("choices") and "结束任务" in p["choices"]
assert "重新登录" in p["choices"], f"顶层步骤应允许重新登录: {p['choices']}"
print(f"  字段 ✓ 标题={ctx['title']}")
print(f"  选项 ✓ {p['choices']}")
print(f"  截图 ✓ {len(ctx['shot'])} 字节 base64")
print(f"  消息 ✓ {p['message']}")

print("=== 2. 选「结束任务」→ 任务终止且给出明确结果 ===")
assert sink.result_obj["error_code"] == "E_USER_ABORT", sink.result_obj
print(f"  result: {sink.result_obj} ✓")

print("=== 3. 选「直接重试」→ 真的重跑下载(不是空转) ===")
sink, human = run(["直接重试", "结束任务"])
assert FakeAdapter.download_calls == 2, f"下载调用次数={FakeAdapter.download_calls}"
assert len([e for e in sink.events if e["type"] == "need_human"]) == 2
print(f"  下载实际重跑 {FakeAdapter.download_calls} 次, 面板出现 2 次 ✓")

print("=== 4. 选「跳过」→ 该步骤标记跳过并明确告知 ===")
sink, human = run(["跳过这家店，继续下一家"])
logs = " ".join(e.get("msg", "") for e in sink.events if e["type"] == "log")
assert sink.result_obj["error_code"] == "E_USER_SKIP", sink.result_obj
assert "已按你的选择跳过" in logs, logs[-200:]
print(f"  result: {sink.result_obj} ✓")

print("=== 5. 真实异常 → 分类准确透传(结构性) ===")
sink, human = run(["结束任务"], raise_mode=True)
p = [e for e in sink.events if e["type"] == "need_human"][0]
ctx = p["context"]
assert ctx["error_code"] == "E_STRUCTURAL", ctx
assert "改版" in ctx["title"], ctx["title"]
assert "未匹配到元素" in ctx["error"], ctx["error"]
assert "发送诊断包" in p["choices"], p["choices"]
print(f"  分类 ✓ {ctx['error_code']} → 标题「{ctx['title']}」")
print(f"  原文透传 ✓ {ctx['error'][:60]}")
print(f"  选项按分类变化 ✓ {p['choices']}")

print("=== 6. 软失败(返回 False, 无异常) → 优雅降级 ===")
sink, human = run(["结束任务"], raise_mode=False)
p = [e for e in sink.events if e["type"] == "need_human"][0]
ctx = p["context"]
assert ctx["error_code"] == "E_UNKNOWN", ctx
assert "没有拿到数据" in ctx["error"], ctx["error"]
print(f"  分类 ✓ {ctx['error_code']}, 提示 ✓「{ctx['title']}」")

print("=== 7. 浏览器被关闭 → 自动重开并继续(不打扰用户, 不给'多等几秒') ===")
sink, human = run([], fail_first=1,
                  error_text="BrowserContext.new_page: Target page, context or browser "
                             "has been closed")
panels = [e for e in sink.events if e["type"] == "need_human"]
logs = " ".join(e.get("msg", "") for e in sink.events if e["type"] == "log")
assert not panels, f"浏览器被关闭属于可自动恢复, 不应弹面板打扰用户: {panels}"
assert "自动化浏览器不可用" in logs, logs[-300:]
assert "浏览器已重开" in logs, logs[-300:]
assert FakeAdapter.launched == 2, f"应重开一次浏览器, 实际 launch={FakeAdapter.launched}"
assert FakeAdapter.download_calls == 2, f"应在重开后自动重试, 实际={FakeAdapter.download_calls}"
assert sink.result_obj["error_code"] != "E_USER_ABORT", sink.result_obj
assert "等待" not in logs.split("自动化浏览器不可用")[-1][:200], "不应给出等待型建议"
print(f"  ✓ 未弹面板(自动恢复) / launch={FakeAdapter.launched} 次 / 下载自动重试 {FakeAdapter.download_calls} 次")
print(f"  ✓ 日志: {[e['msg'][:46] for e in sink.events if e.get('msg', '').startswith(('🔄', '✅ 浏览器'))]}")

print("\n✅ P0 顶层失败面板接线验证通过")
