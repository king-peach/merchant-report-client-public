# -*- coding: utf-8 -*-
"""报表下载入口导航测试(Windows 用户报错: 找不到报表下载入口)

用户账号的入口在「数据中心 → 数据下载」下, 与品牌版(demo_account)的「报表下载」文案/层级不同。
本组测试钉死: ①frame 打分排序 ②多 frame 逐个尝试 ③多文案逐个尝试 ④已在表单页不重复点
⑤失败时报错带现场清单(便于远程定位) ⑥直链失败自动回退菜单导航
"""
import sys
import types
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "kernel"))

from merchant_report.platforms.taobao import TaobaoAdapter


class FakeLocator:
    def __init__(self, els):
        self.els = els

    def count(self):
        return len(self.els)

    def nth(self, i):
        return FakeLocator([self.els[i]])

    def is_visible(self):
        return bool(self.els[0].get("visible", True))

    def click(self, timeout=None):
        if not self.els[0].get("clickable", True):
            raise RuntimeError("element is not clickable")
        self.els[0]["clicked"] = True

    def evaluate(self, js):
        if self.els[0].get("clickable", True):
            raise RuntimeError("无需点父级")
        self.els[0]["clicked"] = True
        return None


class FakeFrame:
    """entries: {入口文案: [元素dict]}; text: 页面可见文本; js_hit: JS 兜底返回的文案"""

    def __init__(self, url="", text="", entries=None, js_hit=""):
        self.url = url
        self.text = text
        self.entries = entries or {}
        self.js_hit = js_hit

    def evaluate(self, js, arg=None):
        if "slice(0, 800)" in js:
            return self.text[:800]
        if "innerText" in js:
            return self.text
        if "labs" in js:                        # JS 兜底点击
            if self.js_hit:
                for els in self.entries.values():
                    for e in els:
                        e["clicked"] = True
            return self.js_hit
        return None

    def get_by_text(self, lab, exact=True):
        return FakeLocator(self.entries.get(lab, []))


class FakePage:
    def __init__(self, frames):
        self.frames = frames
        self.url = "https://melody.shop.ele.me/"


class FakeCtx:
    def __init__(self, frames):
        self.pages = [FakePage(frames)]
        self.handlers = {}

    def on(self, evt, cb):
        self.handlers[evt] = cb


class FakeSink:
    def __init__(self):
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))

    def step_fail(self, *a, **k):
        pass

    def step_start(self, *a, **k):
        pass

    def step_ok(self, *a, **k):
        pass


def ad_with(frames, **kw):
    sink = FakeSink()
    ad = TaobaoAdapter(FakeCtx(frames), sink, types.SimpleNamespace(wait=lambda o: {}), **kw)
    ad._dismiss_popups = lambda: None
    return ad, sink


def els(clickable=True, visible=True):
    return [{"clickable": clickable, "visible": visible, "clicked": False}]


print("=== 1. frame 打分排序: download-center URL 最优先, 无关 frame 被排除 ===")
f_menu = FakeFrame(url="https://x/menu", text="首页 订单管理 数据中心")
f_unrelated = FakeFrame(url="https://x/other", text="欢迎使用 商家版")
f_单店 = FakeFrame(url="https://x/dc/report", text="数据下载 下载管理 报表下载")
f_dc = FakeFrame(url="https://x/download-center/index", text="数据下载")
ad, _ = ad_with([f_menu, f_unrelated, f_dc, f_单店])
ranked = ad._dc_frames()
assert ranked[0] is f_dc, "download-center URL 应排最前"
assert f_unrelated not in ranked, "无关 frame 不该入选"
assert f_单店 in ranked
# 只有"数据中心"菜单、不含任何下载文案的外层 frame 应被排除(它不是下载中心)
assert f_menu not in ranked, "不含下载文案的 frame 不该入选"
print(f"  ✓ 排序 {[r.url.split('/')[-1] for r in ranked]}（无关 frame 已排除）")

print("=== 2. 单店版入口「数据下载」也能点到(旧代码只认「报表下载」) ===")
f = FakeFrame(url="https://x/dc", text="数据下载",
              entries={"数据下载": els()})
ad, sink = ad_with([f])
ad._goto_form(f)
assert f.entries["数据下载"][0]["clicked"] is True, "「数据下载」入口应被点击"
print(f"  ✓ 已点击「数据下载」")

print("=== 3. 文案挂在菜单容器上不可点 → 点其父级 ===")
f = FakeFrame(url="https://x/dc", text="数据下载",
              entries={"数据下载": els(clickable=False)})
ad, sink = ad_with([f])
ad._goto_form(f)
assert f.entries["数据下载"][0]["clicked"] is True
print(f"  ✓ 父级点击成功")

print("=== 4. 第一个 frame 没有入口 → 自动试下一个 frame ===")
f_bad = FakeFrame(url="https://x/a", text="报表下载 下载管理")
f_good = FakeFrame(url="https://x/b", text="数据下载", entries={"数据下载": els()})
ad, sink = ad_with([f_bad, f_good])
got = ad._open_form_frame()
assert got is f_good, "应在候选 frame 中逐个尝试并命中第二个"
print(f"  ✓ 命中 {got.url}")

print("=== 5. 已在表单页(含「下载数据」) → 不重复点击 ===")
f = FakeFrame(url="https://x/dc", text="下载数据 全选 全部 爆品团",
              entries={"报表下载": els()})
ad, sink = ad_with([f])
ad._goto_form(f)
assert f.entries["报表下载"][0]["clicked"] is False, "已在表单页不该再点入口"
print(f"  ✓ 直接通过")

print("=== 6. 全部失败 → 报错带现场清单(远程定位的关键) ===")
f1 = FakeFrame(url="https://x/a", text="数据中心 首页")
f2 = FakeFrame(url="https://x/b", text="订单管理 顾客管理")
ad, sink = ad_with([f1, f2])
try:
    ad._open_form_frame()
    raise AssertionError("应当抛错")
except RuntimeError as e:
    msg = str(e)
    assert "找不到「报表下载」入口" in msg, msg
    assert "现场" in msg, "报错必须带现场清单"
    assert "https://x/a" in msg and "数据中心" in msg, "清单应含 frame url 与可见文本"
    print(f"  ✓ 报错含 {len(msg)} 字符的现场清单")

print("=== 7. 直链失败 → 自动回退菜单导航(数据中心→数据下载) ===")
f = FakeFrame(url="https://x/download-center", text="下载数据", entries={})
ad, sink = ad_with([f], store_id="12345")
calls = {"direct": 0, "menu": 0}

def boom(sid, rounds=3):
    calls["direct"] += 1
    raise RuntimeError("下载中心 iframe 未出现")

def menu(rounds=3):
    calls["menu"] += 1

ad._open_download_center = boom
ad._open_download_center_via_menu = menu
ad._open_form_frame = lambda: f
ad._set_date_range = lambda *a: True
ad._submit_task = lambda *a: True
ad._goto_manager = lambda *a: None
ad._wait_files = lambda *a, **k: ["a.csv"]
ad.ctx.new_cdp_session = lambda pg: types.SimpleNamespace(send=lambda *a, **k: None)
import os
os.makedirs("/tmp/dc_test", exist_ok=True)
ad.download_reports("/tmp/dc_test", date_from=None, date_to=None)
assert calls["direct"] == 1 and calls["menu"] == 1, calls
logs = " ".join(m for _, m in sink.logs)
assert "改用菜单导航" in logs, logs
print(f"  ✓ 直链失败后自动走菜单导航（{calls}）")


print("=== 8. 表单没有该订单类型 → 告警并列出真实选项(不静默跳过) ===")
f = FakeFrame(url="https://x/dc", text="下载数据 订单类型")
f.entries = {}
ad, sink = ad_with([f])
f.locator = lambda sel: types.SimpleNamespace(all=lambda: [])   # 没有 li
f.evaluate = lambda js, arg=None: ([] if "getBoundingClientRect" in js else "")
ok = ad._submit_task(f, "爆品团")
assert ok is False, "没有该选项应返回 False"
warn = " ".join(m for lv, m in sink.logs if lv == "warn")
assert "没有「爆品团」选项" in warn, sink.logs
print(f"  ✓ {warn[:60]}…")

print("=== 9. 一个类型都没选中 → 明确报错(说明没进对页面) ===")
f = FakeFrame(url="https://x/not-dc", text="这里是别的页面")
ad, sink = ad_with([f], store_id="1")
ad._open_download_center = lambda sid, rounds=3: None
ad._open_form_frame = lambda: f
ad._set_date_range = lambda *a: True
ad._submit_task = lambda *a: False
ad.ctx.new_cdp_session = lambda pg: types.SimpleNamespace(send=lambda *a, **k: None)
try:
    ad.download_reports("/tmp/dc_test", date_from=None, date_to=None)
    raise AssertionError("应当抛错")
except RuntimeError as e:
    assert "没有任何可选的订单类型" in str(e), str(e)[:120]
    assert "现场" in str(e), "报错应带现场清单"
    print(f"  ✓ 报错: {str(e)[:56]}…")

print("=== 10. 等文件超时 → 报错带下载管理页快照与判断依据(便于远程定位) ===")
f = FakeFrame(url="https://x/dc", text="下载管理 暂无数据")
ad, sink = ad_with([f])
import os as _os
_os.makedirs("/tmp/dc_test2", exist_ok=True)
try:
    ad._wait_files(f, "/tmp/dc_test2", set(), timeout_s=0, need=2)
    raise AssertionError("应当抛错")
except RuntimeError as e:
    msg = str(e)
    assert "没有产出文件" in msg and "期望 2 份" in msg, msg
    assert "下载管理页快照" in msg, "必须带快照"
    assert "判断依据" in msg, "必须给判断依据(提交没生效/平台生成中/平台拒绝)"
    print(f"  ✓ 报错含快照与判断依据({len(msg)} 字符)")

print("=== 11. 只提交了 1 份(单店版无爆品团) → 等 1 份即可, 不会白等到超时 ===")
f = FakeFrame(url="https://x/dc", text="下载管理 全部_门店下载_1.xlsx 成功")
ad, sink = ad_with([f])
import os as _os
d3 = "/tmp/dc_test3"
_os.makedirs(d3, exist_ok=True)
for x in _os.listdir(d3):
    _os.remove(_os.path.join(d3, x))
before = set()
open(_os.path.join(d3, "全部_门店下载_1.xlsx"), "w").close()
got = ad._wait_files(f, d3, before, timeout_s=5, need=1)
assert got == ["全部_门店下载_1.xlsx"], got
print(f"  ✓ need=1 时立即返回 {got}")


print("=== 12. 订单类型必须精确匹配(「全部」不许落到「全部门店」) ===")


class FakeLi:
    def __init__(self, text, visible=True):
        self.text, self.visible, self.clicked = text, visible, False

    def inner_text(self):
        return self.text

    def is_visible(self):
        return self.visible

    def click(self, timeout=None):
        self.clicked = True


f = FakeFrame(url="https://x/dc", text="下载数据")
f.evaluate = lambda js, arg=None: ({"c": 3, "u": 0} if "checkbox" in js else "")
lis = [FakeLi("全部门店"), FakeLi("按天"), FakeLi("全部")]
f.locator = lambda sel: (types.SimpleNamespace(all=lambda: lis)
                         if sel == "li" else types.SimpleNamespace(
                             first=types.SimpleNamespace(click=lambda *a, **k: None)))
ad, sink = ad_with([f])
ok = ad._submit_task(f, "全部")
assert ok is True
hit = [li.text for li in lis if li.clicked]
assert hit == ["全部"], f"必须精确点「全部」, 实际点到: {hit}（曾误匹配「全部门店」导致页面错乱）"
print(f"  ✓ 精确命中 {hit}（未误点「全部门店」）")


print("=== 13. 下载落地改用 Playwright download 事件(不依赖被新版 Chrome 忽略的 CDP 设置) ===")
f = FakeFrame(url="https://x/download-center", text="下载数据")
ad, sink = ad_with([f], store_id="1")
ad._open_download_center = lambda sid, rounds=3: None
ad._open_form_frame = lambda: f
ad._set_date_range = lambda *a: True
ad._submit_task = lambda *a: True
ad._goto_manager = lambda *a: None
ad._wait_files = lambda *a, **k: ["ok.xlsx"]
import os as _os4
_os4.makedirs("/tmp/dc_test4", exist_ok=True)
# CDP 调用被浏览器拒绝(模拟 Chrome 153) → 不该让任务失败
ad.ctx.new_cdp_session = lambda pg: types.SimpleNamespace(
    send=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("'Browser.setDownloadBehavior' wasn't found")))
ad.download_reports("/tmp/dc_test4", date_from=None, date_to=None)
assert "download" in ad.ctx.handlers, "必须挂载 download 事件接管"
logs = " ".join(m for _, m in sink.logs)
assert "已挂载下载接管" in logs, logs
assert "已由事件接管兜底" in logs, "CDP 被拒应降级提示而非失败"
print("  ✓ download 事件已挂载; CDP 被拒不阻塞")

print("=== 14. 下载文件落盘: 重名自动加序号 ===")
class FakeDownload:
    suggested_filename = "全部_门店下载_1.xlsx"
    def __init__(self, root): self.root, self.saved = root, None
    def save_as(self, p):
        self.saved = p
        # 必须 >1KB: 新版 _save_download 会把空/极小文件当"平台还在生成"丢弃
        # (见 tests/test_empty_download.py)
        with open(p, "wb") as fh:
            fh.write(b"x" * 4096)
import os as _os
d5 = "/tmp/dc_test5"
_os.makedirs(d5, exist_ok=True)
for x in _os.listdir(d5): _os.remove(_os.path.join(d5, x))
ad2, sink2 = ad_with([f])
dl = FakeDownload(d5)
ad2._save_download(dl, d5)
first = _os.path.basename(dl.saved)
dl2 = FakeDownload(d5)
ad2._save_download(dl2, d5)
second = _os.path.basename(dl2.saved)
assert first == "全部_门店下载_1.xlsx" and second != first, (first, second)
assert "已保存下载" in " ".join(m for _, m in sink2.logs)
print(f"  ✓ 落盘 {first} → 重名时 {second}")

print("\n✅ 下载入口导航测试全部通过")
