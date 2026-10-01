# -*- coding: utf-8 -*-
"""美团报表文件落地: 优先**浏览器原生下载**, 失败才退回接口直取(补 Referer)。

真机背景(2026-10-01 诊断包 diag_下载报表_20261001-230733.json):
  `⚠️ 取文件失败 HTTP 403: distribute-platform-pub.sankuai.com/epassport/download…`
  → 报表没进目录 → 任务失败("目录内无当日报表")。CDN 拒直连, 认浏览器现场请求。
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import meituan as mt                           # noqa: E402

XLSX = b"PK\x03\x04" + b"\x00" * 64          # zip/xlsx 魔数
HTML = b"<!DOCTYPE html><html>403</html>"


class FakeDownload:
    def __init__(self, data):
        self.data = data

    def save_as(self, path):
        with open(path, "wb") as fh:
            fh.write(self.data)


class FakeDLMgr:
    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def value(self):
        if self.page.dl == "raise":
            raise RuntimeError("download failed")
        # "PK" → 真 xlsx 字节; 其它("HTML") → 错误页字节
        return FakeDownload(XLSX if self.page.dl == "PK" else HTML)


class FakePage:
    def __init__(self, dl="PK"):
        self.dl = dl
        self.url = "https://waimaieapp.meituan.com/bizdata_pc/report/download"
        self.clicked = []

    def expect_download(self, timeout=None):
        return FakeDLMgr(self)

    def evaluate(self, js, arg=None):
        self.clicked.append(arg)
        return None


class FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    @property
    def ok(self):
        return self.status == 200

    def body(self):
        return self._body


class FakeReq:
    def __init__(self, status=200, body=XLSX):
        self.status, self._body, self.calls, self.headers = status, body, [], None

    def get(self, url, timeout=None, headers=None):
        self.calls.append(url)
        self.headers = headers
        return FakeResp(self.status, self._body)


class FakeCtx:
    def __init__(self, req):
        self.request = req


def mk(page, req):
    ad = mt.MeituanAdapter.__new__(mt.MeituanAdapter)
    logs = []
    ad.page = page
    ad.ctx = FakeCtx(req)
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append((level, m))})()
    return ad, logs


def main():
    # ① 浏览器原生下载成功(真 xlsx) → 返回 True, 且**不再**发接口直连请求
    d = tempfile.mkdtemp()
    p1 = os.path.join(d, "a.xlsx")
    ad, logs = mk(FakePage("PK"), FakeReq())
    assert ad._fetch_file({"url": "https://cdn.example/x"}, p1) is True, logs
    assert os.path.getsize(p1) == len(XLSX)
    assert ad.ctx.request.calls == [], f"浏览器下载成功就不该再直连: {ad.ctx.request.calls}"
    assert any("浏览器下载" in m for _, m in logs), logs
    print("✅ 浏览器原生下载成功 → 落盘且不再走接口直连")

    # ② 浏览器下载抛错 → 退回接口直取, 且**带上了 Referer**
    d = tempfile.mkdtemp()
    p2 = os.path.join(d, "b.xlsx")
    ad, logs = mk(FakePage("raise"), FakeReq(200, XLSX))
    assert ad._fetch_file({"url": "https://cdn.example/y"}, p2) is True, logs
    assert ad.ctx.request.calls == ["https://cdn.example/y"], ad.ctx.request.calls
    assert ad.ctx.request.headers and "Referer" in ad.ctx.request.headers, ad.ctx.request.headers
    print("✅ 浏览器下载失败 → 接口直取兜底(带 Referer)")

    # ③ 浏览器下载到的不是 xlsx(错误页) → 也应退回接口, 不把错误页当报表
    d = tempfile.mkdtemp()
    p3 = os.path.join(d, "c.xlsx")
    ad, logs = mk(FakePage("HTML"), FakeReq(200, XLSX))
    assert ad._fetch_file({"url": "https://cdn.example/z"}, p3) is True, logs
    assert os.path.getsize(p3) == len(XLSX), "应被接口直取的正常文件覆盖"
    assert any("不是 xlsx" in m for _, m in logs), logs
    print("✅ 浏览器下到错误页 → 改走接口直取(不把错误页存成报表)")

    # ④ 两条路都失败 → False + 明确的 403 告警(与真机诊断包一致)
    d = tempfile.mkdtemp()
    p4 = os.path.join(d, "d.xlsx")
    ad, logs = mk(FakePage("raise"), FakeReq(403, HTML))
    assert ad._fetch_file({"url": "https://cdn.example/w"}, p4) is False, logs
    assert any("HTTP 403" in m for _, m in logs), logs
    print("✅ 两条路都失败 → 返回 False 并留下 HTTP 403 告警")

    print("✅ 美团报表取文件(浏览器优先/接口兜底)单测全部通过")


if __name__ == "__main__":
    main()
