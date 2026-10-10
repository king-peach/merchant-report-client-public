# -*- coding: utf-8 -*-
"""美团登录态判定单测(fake page, 不启动浏览器)。

背景(2026-10-09 真机): 旧版在第 6 秒单次采样 body 前 500 字判"是否已登录" ——
慢加载/入口闪现会被误判"未登录", 白白触发账密登录(风控弹滑块)。新的判定:
  · 内容不足 30 字 = 'loading'(绝不能当成已登录)
  · 「账号登录」或「滑块+拖动/验证」 = 'login'
  · 有内容且无登录特征 = 'in'
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms.meituan import MeituanAdapter   # noqa: E402


class FakePage:
    def __init__(self, text=None, boom=False):
        self.text = text
        self.boom = boom

    def evaluate(self, js):
        if self.boom:
            raise RuntimeError("Execution context was destroyed")
        return self.text


def main():
    ad = MeituanAdapter.__new__(MeituanAdapter)

    # ① 内容不足/空 → loading(绝不能骗成已登录)
    assert ad._login_state(FakePage("")) == "loading"
    assert ad._login_state(FakePage("加载中…")) == "loading"
    assert ad._login_state(FakePage(None)) == "loading"
    print("① 空/过短页面 → loading ✓")

    # ② evaluate 抛异常(导航中) → loading
    assert ad._login_state(FakePage(boom=True)) == "loading"
    print("② 页面导航中(evaluate 异常) → loading ✓")

    # ③ 登录页 → login
    assert ad._login_state(FakePage("账号登录\n微信扫码登录\n验证码登录\n请输入手机号或账号密码以便继续")) == "login"
    print("③ 登录页(账号登录) → login ✓")

    # ④ 滑块弹层 → login(拖动/验证+滑块)
    assert ad._login_state(FakePage("请拖动下方滑块完成验证 " + "x" * 50)) == "login"
    print("④ 滑块验证弹层 → login ✓")

    # ⑤ 工作台 → in
    assert ad._login_state(FakePage("工作台 门店管理 经营数据 " + "x" * 80)) == "in"
    print("⑤ 工作台正常内容 → in ✓")

    # ⑥ 普通内容提到"滑块"但无拖动/验证 → 不误判(如设置说明文案)
    t = "帮助中心: 如何关闭滑块相关提醒 " + "y" * 60
    assert ad._login_state(FakePage(t)) == "in"
    print("⑥ 仅含「滑块」无「拖动/验证」→ 不误判 login ✓")

    # ⑦ v3(2026-10-09): 报表页直连优先 —— 会话有效时直接判成功, 绝不打开首页/走账密
    class _FakeCtx:
        def new_page(self):
            class _P:
                def goto(self, *a, **k):
                    raise AssertionError("会话有效时不该打开首页")
            return _P()

    class _FakeSink:
        def __init__(self):
            self.logs = []

        def log(self, msg, level="info"):
            self.logs.append((level, str(msg)))

        def need_human(self, *a, **k):
            raise AssertionError("会话有效时不该找人工")

        def step_fail(self, *a, **k):
            raise AssertionError("会话有效时不该失败")

    class _AdProbe(MeituanAdapter):
        def __init__(self):
            self.sink = _FakeSink()
            self.ctx = _FakeCtx()
            self.hits = []

        def _find_logged_in(self):
            return None

        def _report_page(self, timeout_s=90):
            self.hits.append(("report", timeout_s))
            return object()

    ap = _AdProbe()
    assert ap.ensure_login("", "") is True, "报表页直连成功应返回 True"
    assert ap.hits == [("report", 45)], f"应先以 45s 超时试报表页: {ap.hits}"
    assert any("报表页直连" in m for _, m in ap.sink.logs), "应有「报表页直连」成功日志"
    print("⑦ 报表页直连优先(会话有效→不走首页)✓")

    print("\n✅ 美团登录态判定 全绿")


if __name__ == "__main__":
    main()
