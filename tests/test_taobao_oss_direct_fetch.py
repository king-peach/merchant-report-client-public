# -*- coding: utf-8 -*-
"""淘宝 OSS 报表「接口直取」单测 (2026-10-12 客户机根治)。

现场: 客户机跑淘宝闪购, **报表文件从阿里云 OSS 开始下载的那一瞬, 整个自动化浏览器被关闭**
(browser disconnected → Download.save_as 报 Target closed → 自动重开→重试→再死)。
同机同浏览器美团可跑通 —— 美团取文件走 ctx.request(纯 HTTP, 完全不碰浏览器下载机制)。

对策: 给 context 装路由, OSS 报表文件请求一律 abort, 改由 ctx.request 拉回落盘。
本测试钉死四件事:
  ① 命中判定纯函数(is_oss_report_url): 真 URL 命中, 页面自身请求不命中;
  ② 路由 handler: 命中→abort+记录; 非命中→continue; handler 内异常→continue(绝不放坏页面);
  ③ 直取落盘(_fetch_oss_reports): PK 字节 → 写出且内容一致; 失败重试计数; 成功后不重复拉;
     重名不覆盖; 耗尽 → 回退日志;
  ④ 接线守卫(inspect.getsource): download_reports 装路由 + 有拦截日志; _wait_files 调直取。
"""
import inspect
import os
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import taobao as T                    # noqa: E402

OSS_REAL = ("https://kbtdatacenter.oss-cn-zhangjiakou.aliyuncs.com/2026-10/"
            "2026-10-12/1791820442870/"
            "%E9%97%A8%E5%BA%97%E4%B8%8B%E8%BD%BD_20260926%E8%87%B320260926_%E5%85%A8%E9%83%A8.xlsx")


# ---------- 测试替身 ----------

class FakeRoute:
    """只记 abort/continue, 不碰真浏览器。"""

    def __init__(self, url, exc_on_abort=False):
        self.request = type("R", (), {"url": url})()
        self.aborted = False
        self.continued = False
        self.exc_on_abort = exc_on_abort

    def abort(self):
        if self.exc_on_abort:
            raise RuntimeError("abort 失败")
        self.aborted = True

    def continue_(self):
        self.continued = True


class FakeResp:
    def __init__(self, status=200, body=b""):
        self.status = status
        self._body = body

    @property
    def ok(self):
        return 200 <= self.status < 300

    def body(self):
        return self._body


class FakeRequest:
    """按 URL 返回预设响应; 记录调用次数。"""

    def __init__(self, table):
        self.table = table          # {url: FakeResp}
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append(url)
        r = self.table.get(url)
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise RuntimeError("未知 URL")
        return r


class FakePage:
    def __init__(self, url="https://melody.shop.ele.me/"):
        self.url = url

    def is_closed(self):
        return False


class FakeCtx:
    def __init__(self, request):
        self.request = request
        self.routes = []

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))


class FakeSink:
    def __init__(self):
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))


def mk_adapter(request=None):
    ad = T.TaobaoAdapter.__new__(T.TaobaoAdapter)      # 不走 __init__(不需要浏览器)
    ad.sink = FakeSink()
    ad.page = FakePage()
    ad.ctx = FakeCtx(request or FakeRequest({}))
    ad._oss_urls = []
    ad._oss_gave_up = set()
    ad._oss_pull_tries = {}
    ad._oss_pull_done = set()
    return ad


def logs_text(ad, level=None):
    return " ".join(m for lv, m in ad.sink.logs if level is None or lv == level)


# ---------- ① 命中判定纯函数 ----------

def test_predicate():
    hit = [
        OSS_REAL,
        # 平台换文案 → 末段中文不是「门店下载」, 但 .xlsx 后缀仍在 → 仍要命中
        "https://kbtdatacenter.oss-cn-zhangjiakou.aliyuncs.com/2026-10/12/9/report_20260926.xlsx",
        # 中文未编码 + 带签名查询串
        "https://cdn.oss-cn-hangzhou.aliyuncs.com/a/门店下载_20260926至20260926_全部.xlsx?sign=x",
    ]
    for u in hit:
        assert T.is_oss_report_url(u), f"应命中: {u}"

    miss = [
        "https://lsycm.alibaba.com/api/download/request",
        "https://lsycm.alibaba.com/api/download/queryTasks",
        "https://fourier.taobao.com/x",
        "https://gm.mmstat.com/y",
        "https://melody.shop.ele.me/app/shop/1/downloadCenter",
        # 是 OSS 但不是报表后缀(图片/JS) → 不该拦
        "https://img.oss-cn-hangzhou.aliyuncs.com/a/logo.png",
        "", None,
    ]
    for u in miss:
        assert not T.is_oss_report_url(u), f"不该命中: {u!r}"
    print("✅ ① 命中判定: 真 OSS 报表 URL 命中; 页面自身请求(lsycm/fourier/mmstat/图片)不命中")


# ---------- ② 路由 handler ----------

def test_handler():
    ad = mk_adapter()
    # 命中 → abort + 记录 + 日志
    r = FakeRoute(OSS_REAL)
    ad._on_route(r)
    assert r.aborted and not r.continued, "命中的 OSS 报表请求必须 abort"
    assert ad._oss_urls == [OSS_REAL], ad._oss_urls
    assert "已拦下浏览器直下" in logs_text(ad)
    # 去重
    ad._on_route(FakeRoute(OSS_REAL))
    assert len(ad._oss_urls) == 1, "同一 URL 不重复记录"
    # 非命中 → continue(页面请求绝不能被影响)
    r2 = FakeRoute("https://lsycm.alibaba.com/api/download/request")
    ad._on_route(r2)
    assert r2.continued and not r2.aborted, "页面自身请求必须放行"
    # handler 内异常 → 仍然 continue(不许把页面弄坏)
    class Boom:
        @property
        def request(self):
            raise RuntimeError("拿 request 也炸了")

        def continue_(self):
            pass
    ad._on_route(Boom())   # 不抛异常 = 通过
    print("✅ ② handler: 命中→abort+记录; 非命中→continue; 内部异常一律 continue 不炸页面")


# ---------- ③ 直取落盘 ----------

def test_fetch_ok():
    d = tempfile.mkdtemp(prefix="oss_dl_")
    try:
        pk = b"PK\x03\x04" + b"\x00" * 4096
        ad = mk_adapter(FakeRequest({OSS_REAL: FakeResp(200, pk)}))
        ad._oss_urls = [OSS_REAL]
        got = ad._fetch_oss_reports(d)
        assert got == ["门店下载_20260926至20260926_全部.xlsx"], got
        p = os.path.join(d, got[0])
        assert open(p, "rb").read() == pk, "落盘内容必须与响应体一致"
        assert "接口直取成功" in logs_text(ad)
        # 成功后再轮询: 不重复拉(请求计数不增)
        ad._fetch_oss_reports(d)
        assert len(ad.ctx.request.calls) == 1, ad.ctx.request.calls
        # 重名不覆盖
        ad2 = mk_adapter(FakeRequest({OSS_REAL: FakeResp(200, pk)}))
        ad2._oss_urls = [OSS_REAL]
        ad2._fetch_oss_reports(d)
        assert len(ad2.ctx.request.calls) == 0, "目录里已有同名文件 → 不再重复拉"
        print("✅ ③a 直取: PK 字节落盘且内容一致; 成功后不重复拉; 重名不覆盖")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_fetch_retry():
    d = tempfile.mkdtemp(prefix="oss_retry_")
    try:
        # 前 3 次 HTTP 500, 第 4 次成功
        class Flaky(FakeRequest):
            def get(self, url, timeout=None, headers=None):
                self.calls.append(url)
                if len(self.calls) < 4:
                    return FakeResp(500, b"err")
                return FakeResp(200, b"PK\x03\x04" + b"\x00" * 4096)
        ad = mk_adapter(Flaky({}))
        ad._oss_urls = [OSS_REAL]
        for i in range(3):
            assert ad._fetch_oss_reports(d) == [], f"第{i+1}轮不该成功"
        assert ad._oss_pull_tries[OSS_REAL] == 3, ad._oss_pull_tries
        assert "接口直取未成(第1次)" in logs_text(ad)
        assert ad._fetch_oss_reports(d), "第4轮应成功"
        assert ad._oss_pull_tries[OSS_REAL] == 4
        # 非 xlsx 响应(错误页/登录页)不算成功
        ad2 = mk_adapter(FakeRequest({OSS_REAL: FakeResp(200, b"<html>err")}))
        ad2._oss_urls = [OSS_REAL]
        d2 = tempfile.mkdtemp(prefix="oss_retry2_")
        assert ad2._fetch_oss_reports(d2) == []
        assert "响应不是 xlsx" in logs_text(ad2), logs_text(ad2)
        # 过小的 xlsx(平台还在生成) → 丢弃且不记成功
        ad3 = mk_adapter(FakeRequest({OSS_REAL: FakeResp(200, b"PK\x03\x04" + b"\x00" * 10)}))
        ad3._oss_urls = [OSS_REAL]
        d3 = tempfile.mkdtemp(prefix="oss_retry3_")
        assert ad3._fetch_oss_reports(d3) == []
        assert "文件过小" in logs_text(ad3), logs_text(ad3)
        shutil.rmtree(d2, ignore_errors=True)
        shutil.rmtree(d3, ignore_errors=True)
        print("✅ ③b 重试: 失败逐轮计数; 非 xlsx / 过小文件都不算成功")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_fetch_exhausted():
    d = tempfile.mkdtemp(prefix="oss_exh_")
    try:
        ad = mk_adapter(FakeRequest({OSS_REAL: RuntimeError("Connection reset by peer")}))
        ad._oss_urls = [OSS_REAL]
        for _ in range(ad._oss_pull_max_tries()):
            ad._fetch_oss_reports(d)
        assert ad._oss_pull_tries[OSS_REAL] == ad._oss_pull_max_tries(), ad._oss_pull_tries
        # 再轮询一次 → 判放弃, 不再请求, 日志明确"回退浏览器直下"
        before = len(ad.ctx.request.calls)
        ad._fetch_oss_reports(d)
        assert len(ad.ctx.request.calls) == before, "放弃后不许再拉"
        assert "回退浏览器直下" in logs_text(ad, level="warn"), ad.sink.logs
        assert OSS_REAL in ad._oss_pull_done
        # 放弃后 → 路由对这条 URL 放行(承诺的"回退浏览器直下"必须真的生效, 不许假回退)
        assert OSS_REAL in ad._oss_gave_up
        r = FakeRoute(OSS_REAL)
        ad._on_route(r)
        assert r.continued and not r.aborted, "放弃后必须放行浏览器直下(否则是假回退)"
        print(f"✅ ③c 耗尽: 试满 {ad._oss_pull_max_tries()} 轮后放弃, 日志明示回退且路由确实放行")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------- ④ 接线守卫 ----------

def test_wiring():
    src = inspect.getsource(T.TaobaoAdapter.download_reports)
    assert 'self.ctx.route("**/*"' in src, "download_reports 必须装 context 级路由"
    assert "已启用 OSS 直取拦截" in src, "挂载成功要有日志"
    assert "_on_route" in src
    wf = inspect.getsource(T.TaobaoAdapter._wait_files)
    assert "_fetch_oss_reports" in wf, "_wait_files 轮询里必须调用接口直取"
    assert "接口直取" in wf
    rt = inspect.getsource(T.TaobaoAdapter._on_route)
    assert "abort()" in rt and "continue_()" in rt, "命中 abort / 其余 continue"
    assert "绝不" in rt, "必须注释写明 handler 里不许发网络请求"
    # 边界: 没触发 OSS 下载时一切照旧(无 URL → 直取空转返回, 不报错)
    ad = mk_adapter()
    assert ad._fetch_oss_reports("/tmp") == []
    print("✅ ④ 接线: 路由安装 + 拦截日志 + _wait_files 直取; 无 OSS 下载时无操作")


def main():
    test_predicate()
    test_handler()
    test_fetch_ok()
    test_fetch_retry()
    test_fetch_exhausted()
    test_wiring()
    print("\n✅ 淘宝 OSS 接口直取 全绿")


if __name__ == "__main__":
    main()
