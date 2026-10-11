# -*- coding: utf-8 -*-
"""美团报表取文件: **接口直取优先** + 浏览器下载隔离进一次性页 + 内容校验。

真机背景(2026-10-11 用户报): 美团外卖在**另一商家账号 / Windows 环境**会把页面跳到
distribute-platform-pub.sankuai.com 或 epassport 登录页 —— 实验
(exp_download_behavior.py case C) 钉死机制: 对 CDN 签名链接做锚点点击, 在**下载会话
未授权**的机器上不会变成下载, 而会退化为**整页导航**(Chromium 跟着 302 走); 旧版在
工作页里点 → 工作页被跳走 + 白等 180s 才兜底。

本测试守住 5 条不变量:
  ① 直取成功 → 不碰任何页面(零跳转风险);
  ② 直取失败 → 浏览器下载**只在一次性页**发生, 工作页 self.page 全程不被读写;
  ③ 链接被整页导航(会话未授权) → 立即判失败 + 明确日志, 不干等、不把登录页当报表;
  ④ 浏览器下到错误页 → 丢弃 + False(绝不存成报表);
  ⑤ 真 CSV 按 gb18030「日期」识别(旧代码写死 PK 魔数 → 每次都误判失败)。
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan as mt                           # noqa: E402

CSV = "日期,门店,金额,单量\n2026-10-01,A店,100,3\n".encode("gb18030")   # 真美团导出格式
XLSX = b"PK\x03\x04" + b"\x00" * 64                                    # 兼容 xlsx
HTML = b"<!DOCTYPE html><html>403</html>"
LOGIN_PAGE = "https://epassport.meituan.com/portal/login?service=com.sankuai.sec.distribute.platform"


class FakeDownload:
    def __init__(self, data):
        self.data = data

    def save_as(self, path):
        with open(path, "wb") as fh:
            fh.write(self.data)


class FakeTmpPage:
    """一次性标签页。mode: download=触发下载事件 / navigate=整页导航 / timeout=什么都没发生。"""

    def __init__(self, mode="download", body=CSV):
        self.mode, self.body = mode, body
        self.url = "about:blank"
        self.closed = False
        self._handlers = {}

    def on(self, ev, cb):
        self._handlers[ev] = cb

    def evaluate(self, js, arg=None):
        if self.mode == "download":
            cb = self._handlers.get("download")
            if cb:
                cb(FakeDownload(self.body))
        elif self.mode == "navigate":
            self.url = LOGIN_PAGE          # 点击 → Chromium 把这一页导航走了
        return None

    def wait_for_timeout(self, ms):
        return None

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


class FakePage:
    """工作页: 只用来提供 Referer。任何导航/点击都记进 self.touched(应为空)。"""

    def __init__(self):
        self.url = "https://waimaieapp.meituan.com/bizdata_pc/report/download?acctId=1"
        self.touched = []

    def is_closed(self):
        return False

    def evaluate(self, *a, **k):                     # 工作页绝不该被用来点下载链接
        self.touched.append(a)
        raise AssertionError("工作页不该被 evaluate(下载已隔离到一次性页)")

    def goto(self, *a, **k):
        self.touched.append(a)
        raise AssertionError("工作页不该被导航")


class FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    @property
    def ok(self):
        return self.status == 200

    def body(self):
        return self._body


class FakeReq:
    def __init__(self, status=200, body=CSV):
        self.status, self._body, self.calls, self.headers = status, body, [], None

    def get(self, url, timeout=None, headers=None):
        self.calls.append(url)
        self.headers = headers
        return FakeResp(self.status, self._body)


class FakeCtx:
    def __init__(self, req, tmp_mode="download", tmp_body=CSV):
        self.request = req
        self._tmp_mode, self._tmp_body = tmp_mode, tmp_body
        self.tmp_pages = []

    def new_page(self):
        p = FakeTmpPage(self._tmp_mode, self._tmp_body)
        self.tmp_pages.append(p)
        return p


def mk(req, tmp_mode="download", tmp_body=CSV):
    ad = mt.MeituanAdapter.__new__(mt.MeituanAdapter)
    logs = []
    ad.page = FakePage()
    ad.ctx = FakeCtx(req, tmp_mode, tmp_body)
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append((level, m))})()
    return ad, logs


def main():
    # ① 接口直取成功(真 CSV) → True; 不开临时页(零跳转风险)
    d = tempfile.mkdtemp()
    p1 = os.path.join(d, "a.csv")
    ad, logs = mk(FakeReq(200, CSV))
    assert ad._fetch_file({"url": "https://cdn.example/x"}, p1) is True, logs
    assert open(p1, "rb").read() == CSV
    assert ad.ctx.request.calls == ["https://cdn.example/x"], ad.ctx.request.calls
    assert ad.ctx.tmp_pages == [], "直取成功不该再开浏览器临时页"
    assert ad.page.touched == [], "工作页全程不该被动过"
    print("✅ 直取成功(真 CSV) → 落盘且不碰任何页面")

    # ② 直取 403(CDN 拒直连) → 退回浏览器下载, 且**只在一次性页**, 工作页零动作
    d = tempfile.mkdtemp()
    p2 = os.path.join(d, "b.csv")
    ad, logs = mk(FakeReq(403, HTML))
    assert ad._fetch_file({"url": "https://cdn.example/y"}, p2) is True, logs
    assert open(p2, "rb").read() == CSV
    assert len(ad.ctx.tmp_pages) == 1 and ad.ctx.tmp_pages[0].closed, "临时页必须开且用后关闭"
    assert ad.page.touched == [], "工作页全程不该被动过(跳转只牺牲临时页)"
    assert any("浏览器下载" in m for _, m in logs), logs
    print("✅ 直取 403 → 浏览器下载(一次性页), 工作页零动作")

    # ③ 直取 200 但是登录页 HTML → 认得出不是报表 → 走浏览器下载
    d = tempfile.mkdtemp()
    p3 = os.path.join(d, "c.csv")
    ad, logs = mk(FakeReq(200, HTML))
    assert ad._fetch_file({"url": "https://cdn.example/z"}, p3) is True, logs
    assert open(p3, "rb").read() == CSV
    assert any("不是报表" in m for _, m in logs), logs
    print("✅ 直取拿到登录页 → 识别为非报表 → 改走浏览器下载")

    # ④ 直取失败 + 点击变成整页导航(会话未授权) → False + 明确日志; 不干等、不存登录页
    d = tempfile.mkdtemp()
    p4 = os.path.join(d, "d.csv")
    ad, logs = mk(FakeReq(403, HTML), tmp_mode="navigate")
    assert ad._fetch_file({"url": "https://cdn.example/w"}, p4) is False, logs
    assert not os.path.exists(p4), "登录页绝不能被存成报表"
    assert any("整页导航" in m for _, m in logs), logs
    assert ad.page.touched == [], "工作页全程不该被动过"
    print("✅ 链接被整页导航 → 立即判失败(隔离在临时页, 工作页无感)")

    # ⑤ 直取失败 + 浏览器下载超时(无下载事件/无导航) → False + 超时日志
    d = tempfile.mkdtemp()
    p5 = os.path.join(d, "e.csv")
    ad, logs = mk(FakeReq(403, HTML), tmp_mode="timeout")
    assert ad._fetch_via_tmp_page("https://cdn.example/v", p5, timeout_s=1) is False, logs
    assert any("超时" in m for _, m in logs), logs
    print("✅ 浏览器下载超时 → False + 超时日志")

    # ⑥ 浏览器下到错误页(非报表) → 丢弃 + False
    d = tempfile.mkdtemp()
    p6 = os.path.join(d, "f.csv")
    ad, logs = mk(FakeReq(403, HTML), tmp_mode="download", tmp_body=HTML)
    assert ad._fetch_file({"url": "https://cdn.example/u"}, p6) is False, logs
    assert not os.path.exists(p6), "错误页必须丢弃"
    assert any("丢弃" in m for _, m in logs), logs
    print("✅ 浏览器下到错误页 → 丢弃且不落盘")

    # ⑦ 内容识别: 真 CSV(gb18030 日期) / xlsx(PK) 都认; HTML 不认
    assert mt.MeituanAdapter._looks_like_report(CSV[:16]) is True
    assert mt.MeituanAdapter._looks_like_report(XLSX[:16]) is True
    assert mt.MeituanAdapter._looks_like_report(HTML[:16]) is False
    print("✅ 内容识别: gb18030「日期」CSV / PK xlsx 认, HTML 不认")

    print("✅ 美团报表取文件(直取优先/浏览器隔离/内容校验)单测全部通过")


if __name__ == "__main__":
    main()
