# -*- coding: utf-8 -*-
"""美团外卖报表页 acctId 处理单测。

背景(用户实报): 打开 https://waimaieapp.meituan.com/igate/bizdata/report/download
返回 {"code":30000,"msg":"acctId不存在","result":""} —— 裸链缺账号ID。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kernel"))

from merchant_report.platforms import meituan as M


class FakeSink:
    def __init__(self):
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))

    def step_start(self, *a, **k):
        pass

    def step_ok(self, *a, **k):
        pass

    def step_fail(self, *a, **k):
        pass

    def texts(self):
        return " ".join(m for _, m in self.logs)


class FakePage:
    """可编程页面: goto 后 body 文本 / JS 返回值可配。"""

    def __init__(self, url="https://e.waimai.meituan.com/", acct_js="", body="", portal_acct=""):
        self.url = url
        self.acct_js = acct_js          # 当前页面上能取到的 acctId(空=取不到)
        self.body = body
        self.portal_acct = portal_acct  # 访问门户首页后才落到存储里的 acctId
        self.gotos = []
        self.closed = False

    def is_closed(self):
        return self.closed

    def goto(self, url, **k):
        self.gotos.append(url)
        self.url = url
        if url.startswith("https://e.waimai.meituan.com") and self.portal_acct:
            self.acct_js = self.portal_acct
        return None

    def evaluate(self, js, *a):
        if "acct" in js and "location.href" in js:
            return self.acct_js
        if "querySelector('span.custom-checkbox-label')" in js:
            return getattr(self, "form_ok", False)     # 报表页表单是否已渲染
        if "documentElement.innerHTML" in js:
            return getattr(self, "html", "")
        if "document.body" in js:
            return self.body
        return False


class FakeCtx:
    def __init__(self, pages, cookies=None):
        self.pages = pages
        self._cookies = list(cookies or [])
        self.added = []

    def cookies(self):
        return list(self._cookies)

    def add_cookies(self, items):
        self.added.extend(items)
        self._cookies.extend(items)

    def new_page(self):
        p = FakePage()
        self.pages.append(p)
        return p


def adapter(pages):
    ad = M.MeituanAdapter(FakeCtx(pages), FakeSink(), None)
    return ad, ad.sink


print("=== 1. acctId 已能取到 → 报表页 URL 必须带 ?acctId= ===")
p = FakePage(url="https://e.waimai.meituan.com/#/report?acctId=88430021")
ad, sink = adapter([p])
ad.page = p
ad.acct_id = "88430021"
p.body = ""       # 表单页
ad._report_page = M.MeituanAdapter._report_page.__get__(ad)
try:
    ad._report_page(timeout_s=1)
except RuntimeError as e:
    assert "未就绪" in str(e), e
assert p.gotos[0].endswith("?acctId=88430021"), p.gotos
print("  ✓ goto:", p.gotos[0])

print("=== 2. 会话里没 acctId → 回门户页提取后再打开 ===")
portal = FakePage(url="https://e.waimai.meituan.com/", acct_js="", portal_acct="88430021")
ad2, sink2 = adapter([portal])
ad2.page = portal
try:
    ad2._report_page(timeout_s=1)
except RuntimeError:
    pass
assert "e.waimai.meituan.com" in portal.gotos[0], portal.gotos        # 先去门户
assert any("acctId=88430021" in u for u in portal.gotos), portal.gotos  # 再带 ID 打开报表页
assert "从页面取到账号ID" in sink2.texts()
print("  ✓ 取到门店账号ID并带参打开:", [u[:78] for u in portal.gotos])

print("=== 3. 裸 JSON 错误页(code:30000 acctId不存在) → 立刻报错, 不干等 90 秒 ===")
p3 = FakePage(url="https://e.waimai.meituan.com/")
p3.body = '{"code":30000,"msg":"acctId不存在","result":""}'
ad3, sink3 = adapter([p3])
ad3.page = p3
p3.acct_js = ""      # 取不到 ID → 走裸链
t0 = time.time()
try:
    ad3._report_page(timeout_s=90)
    raise AssertionError("应当抛错")
except RuntimeError as e:
    msg = str(e)
    dt = time.time() - t0
assert dt < 40, f"应尽早报错(含门户页提取+一次重试; 实际 {dt:.1f}s), 不能干等 90s"
assert "acctId不存在" in msg, "原始后端错误要保留给人看"
assert "登录态失效" in msg, "要按实测结论定性为会话问题, 而不是含糊的未就绪"
assert "重新登录" in msg, "要给出可执行的下一步"
assert "e.waimai.meituan.com/" in p3.gotos[0], "取不到 ID 时回退裸链"
print(f"  ✓ {dt:.1f}s 内报错: {msg[:96]}…")

print("=== 4. 取不到 acctId 也要留下「试过哪些来源」的痕迹 ===")
p4 = FakePage(url="https://e.waimai.meituan.com/", acct_js="")
ad4, sink4 = adapter([p4])
ad4._discover_acct_id(p4)
assert "已尝试" in sink4.texts() and "cookie" in sink4.texts(), sink4.texts()
print("  ✓", [m for _, m in sink4.logs if "acctId" in m][-1][:80])

print("=== 5. 第一次被拒 → 访问门户页刷新会话 → 重试成功(自愈) ===")


class SmartPage:
    """第一次访问门户页拿不到 acctId, 第二次能拿到(模拟会话上下文缺失→刷新后恢复)。"""

    def __init__(self):
        self.url = "https://e.waimai.meituan.com/"
        self.gotos = []
        self.portal_accts = ["", "77788899"] if True else []

    def is_closed(self):
        return False

    def goto(self, url, **k):
        self.gotos.append(url)
        self.url = url
        if url.startswith("https://e.waimai.meituan.com"):
            if self.portal_accts:
                self._acct = self.portal_accts.pop(0)
        return None

    def evaluate(self, js, *a):
        if "acct" in js and "location.href" in js:
            return getattr(self, "_acct", "")
        if "querySelector" in js:
            return "acctId=" in self.url          # 带 acctId 才有表单
        if "document.body" in js:
            return "" if "acctId=" in self.url else '{"code":30000,"msg":"acctId不存在","result":""}'
        return False


sp = SmartPage()
ad5, sink5 = adapter([sp])
ad5.page = sp
ad5._report_page(timeout_s=2)
assert any("acctId=77788899" in u for u in sp.gotos), sp.gotos
assert "访问门户页刷新会话上下文后重试一次" in sink5.texts()
print("  ✓ 自愈成功, URL 序列:", [u[-58:] for u in sp.gotos])

print("=== 6. wmPoiId cookie 缺失 → 自动补 -1(全部门店), 两个域名都补 ===")
# 实测依据: 删掉 wmPoiId cookie 后报表页不再渲染表单并报「参数异常:wmPoid,当前wmPoid为:null」
p6 = FakePage(url="https://e.waimai.meituan.com/")
ctx6 = FakeCtx([p6], cookies=[{"name": "acctId", "value": "222222222",
                                "domain": "e.waimai.meituan.com", "path": "/"}])
ad6 = M.MeituanAdapter(ctx6, FakeSink(), None)
ad6.page = p6
ad6._ensure_store_cookies("222222222")
poi = [c for c in ctx6.added if c["name"] == "wmPoiId"]
assert len(poi) == 2 and {c["domain"] for c in poi} == {"waimaieapp.meituan.com", "e.waimai.meituan.com"}, ctx6.added
assert all(c["value"] == "-1" for c in poi), poi
assert "门店上下文缺失" in ad6.sink.texts()
print(f"  ✓ 补了 {len(poi)} 条: " + ", ".join(f"{c['name']}={c['value']}@{c['domain']}" for c in poi))

print("=== 7. cookie 已存在 → 不重复写; acctId 不一致 → 纠正 ===")
ctx7 = FakeCtx([p6], cookies=[{"name": "wmPoiId", "value": "-1", "domain": "e.waimai.meituan.com", "path": "/"},
                              {"name": "acctId", "value": "999", "domain": "e.waimai.meituan.com", "path": "/"}])
ad7 = M.MeituanAdapter(ctx7, FakeSink(), None)
ad7.page = p6
ad7._ensure_store_cookies("222222222")
assert not [c for c in ctx7.added if c["name"] == "wmPoiId"], "已存在就不该再写"
assert len([c for c in ctx7.added if c["name"] == "acctId"]) == 2, ctx7.added
print("  ✓ 只在需要时写")

print("=== 8. 报表页报「参数异常:wmPoid」时, 报错要把页面原话带出来 ===")
p8 = FakePage(url="https://waimaieapp.meituan.com/igate/bizdata/report/download")
p8.body = "参数异常:wmPoid,当前wmPoid为：【null】"
ad8, sink8 = adapter([p8])
ad8.page = p8
ad8.acct_id = "222222222"
try:
    ad8._report_page(timeout_s=1)
    raise AssertionError("应当抛错")
except RuntimeError as e:
    msg = str(e)
assert "wmPoid" in msg and "参数异常" in msg, msg
assert "门店上下文" in msg, "要给出判定(门店上下文缺失), 不能只说未就绪"
assert "全部门店" in msg, "要给出可执行的下一步"
print("  ✓ 报错含页面原话 + 判定:", msg[:88].replace("\n", " "), "…")

print("=== 9. 报表页回 logon 信号(会话未认证该应用) → 明确说「登录态未覆盖报表应用」===")
p9 = FakePage(url="https://waimaieapp.meituan.com/")
p9.acct_js = ""          # 无 acctId → 走裸链
p9.body = ""
p9.html = "<script>window.parent.postMessage(JSON.stringify({'method': 'logon'}), '*')</script>"
ad9, sink9 = adapter([p9])
ad9.page = p9
try:
    ad9._report_page(timeout_s=1)
    raise AssertionError("应当抛错")
except RuntimeError as e:
    m9 = str(e)
assert "logon" in m9 and "登录" in m9, m9
assert "e.waimai.meituan.com" in m9, "要指明去哪重登"
print("  ✓ ", m9[:96], "…")

print("\n✅ 美团 acctId 处理测试全部通过")
