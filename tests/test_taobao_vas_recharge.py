# -*- coding: utf-8 -*-
"""淘宝充值(GK): 增值服务页「消费记录」→ 当日充值金额(2026-10-13 修复后模板列位: 旧 GL → GK)。

真机表结构(2026-10-08, shop 555555555, 用户指路 /app/shop/<id>/vas → 消费记录):
    表头: 时间 | 消费类型 | 消费金额 | 余额 | 操作
    ['2026-10-07',          '推广消费\\n推广消费',   '-246.49', '721.97',  '详情']
    ['2026-10-05 12:22:13', '现金充值\\n支付宝充值',  '+400.00', '1390.13', '_']
口径: 只认「类型含充值」且金额带 '+' 的行; 该日无充值 → 留空(不臆造 0); 同日多笔累加。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "kernel"))
from merchant_report.platforms import taobao as T   # noqa: E402

ROWS = [
    ['2026-10-08', '推广消费\n推广消费', '-62.35', '', '详情'],
    ['2026-10-07', '推广消费\n推广消费', '-246.49', '721.97', '详情'],
    ['2026-10-06', '推广消费\n推广消费', '-204.71', '968.46', '详情'],
    ['2026-10-05', '推广消费\n推广消费', '-216.96', '1173.17', '详情'],
    ['2026-10-05 12:22:13', '现金充值\n支付宝充值', '+400.00', '1390.13', '_'],
]


def _vas_ledger_cases():
    """任务 J: 「表读到了但窗口不含目标日」→ 必须留覆盖证据 + 翻页尝试, 失败一律留空。"""
    T.time.sleep = lambda *_: None          # 瞬时化(假件驱动)

    def mk(page):
        a = T.TaobaoAdapter.__new__(T.TaobaoAdapter)   # 不跑 __init__(不碰浏览器)
        a.sink = Log()
        a.ctx = type("C", (), {"new_page": lambda _s: page})()
        return a, a.sink

    # ① 窗口覆盖目标日且有充值行 → 直取(不翻页)
    p = FakeVasPage(ROWS)
    a, lg = mk(p)
    r = a._read_vas_ledger("555555555", "2026-10-05")
    assert r["GK"] == 400.0, r
    assert p.more_clicks == 0, "窗口已覆盖时不该翻页"
    print("① 窗口覆盖 + 有充值行 → 直取 GK=400.0(不翻页) ✓")

    # ② 窗口不含目标日 + 没有翻页控件 → 留空 + warn, 且**必须打出覆盖证据**
    p2 = FakeVasPage([r for r in ROWS if r[0] >= "2026-10-06"], more_btn=False)
    a2, lg2 = mk(p2)
    r2 = a2._read_vas_ledger("555555555", "2026-09-15")
    assert r2["GK"] is None and r2["table_found"] is True, r2
    assert r2.get("date_min") == "2026-10-06" and r2.get("date_max") == "2026-10-08", r2
    txt2 = " ".join(m for _, m in lg2.lines)
    assert "早于" in txt2 and "2026-10-06~2026-10-08" in txt2, txt2
    assert "没找到「加载更多/下一页」控件" in txt2, txt2
    assert any(lv == "warn" and "留空" in m for lv, m in lg2.lines), lg2.lines
    print("② 窗口不含目标日 + 无翻页控件 → 留空 + 覆盖证据(2026-10-06~2026-10-08) + warn ✓")

    # ③ 翻页能覆盖目标日 → 翻页后取到充值(每轮记日志)
    p3 = FakeVasPage([r for r in ROWS if r[0] >= "2026-10-06"], more_btn=True,
                     more_rows=[['2026-09-15 10:00:00', '现金充值', '+800.00', '900.00', '_']])
    a3, lg3 = mk(p3)
    r3 = a3._read_vas_ledger("555555555", "2026-09-15")
    assert p3.more_clicks == 1, p3.more_clicks
    assert r3["GK"] == 800.0, r3
    txt3 = " ".join(m for _, m in lg3.lines)
    assert "尝试翻页/筛选覆盖" in txt3 and "加载更多" in txt3, txt3
    assert "翻页后覆盖" in txt3 and "命中该日充值" in txt3, txt3
    assert "消费记录页控件" in txt3, f"翻页前必须先探测控件并记日志: {txt3}"
    print("③ 窗口不覆盖 → 探测控件 + 翻页 → 覆盖后取到 GK=800.0(每轮记日志) ✓")

    # ④ 翻页到尽头仍不覆盖 → 留空 + warn(不拿别日值顶替)
    p4 = FakeVasPage([r for r in ROWS if r[0] >= "2026-10-06"], more_btn=True, more_rows=None)
    a4, lg4 = mk(p4)
    r4 = a4._read_vas_ledger("555555555", "2026-09-15")
    assert r4["GK"] is None, r4
    txt4 = " ".join(m for _, m in lg4.lines)
    assert "已到尽头" in txt4, txt4
    assert any(lv == "warn" and "翻页也没覆盖到" in m and "留空" in m for lv, m in lg4.lines), lg4.lines
    print("④ 翻页到尽头仍不覆盖 → 留空 + warn(不用别日值顶替) ✓")

    # ⑤ 窗口覆盖目标日、当天确实没充值 → 留空, 但日志要区分「在窗口内」
    p5 = FakeVasPage([r for r in ROWS if r[0] >= "2026-10-06"], more_btn=False)
    a5, lg5 = mk(p5)
    r5 = a5._read_vas_ledger("555555555", "2026-10-07")
    assert r5["GK"] is None and r5.get("window_ok") is True, r5
    txt5 = " ".join(m for _, m in lg5.lines)
    assert "已覆盖 2026-10-07" in txt5 and "确实没有充值行" in txt5, txt5
    print("⑤ 窗口覆盖但没有充值行 → 留空 + 明确「窗口内确实没有」 ✓")

    # ⑥ 表格压根没读到 → warn 指 iframe(与"没有充值行"分开, 不许笼统)
    p6 = FakeVasPage([], table_found=False)
    a6, lg6 = mk(p6)
    r6 = a6._read_vas_ledger("555555555", "2026-10-05")
    assert r6["GK"] is None and r6["table_found"] is False, r6
    assert any(lv == "warn" and "iframe" in m for lv, m in lg6.lines), lg6.lines
    print("⑥ 表没读到 → warn 指 iframe(与「没有充值行」区分) ✓")

    # ⑦ 调用侧日志必须带覆盖证据(真机 15/15 那句原来只有结论没有证据)
    cps = inspect.getsource(T.TaobaoAdapter.capture_per_store)
    assert "表格覆盖" in cps and "window_ok" in cps, "调用侧日志要带表格日期覆盖证据"
    assert "vas_ledger_dates" in inspect.getsource(T.TaobaoAdapter._read_vas_ledger)
    src = inspect.getsource(T.TaobaoAdapter._read_vas_ledger)
    assert "_vas_try_load_more" in src and "_vas_probe_controls" in src, "翻页 + 控件探测必须接线"
    assert "max_rounds" in inspect.getsource(T.TaobaoAdapter._vas_try_load_more), "翻页要有轮次上限"
    print("⑦ 调用侧日志带覆盖证据 + 探测/翻页接线(有上限) ✓")

    # ⑧ vas_ledger_dates 纯函数边界(真机有 '2026-1-5 09:00:00' 这种不补零写法 → 必须认出来)
    d = T.vas_ledger_dates
    assert d(ROWS) == ("2026-10-05", "2026-10-08"), d(ROWS)
    assert d([['2026-1-5 09:00:00', '充值', '+1']]) == ("2026-01-05", "2026-01-05"), d(ROWS[:0])
    assert d([]) == (None, None) and d(None) == (None, None)
    assert d([None, [], ["x"], ["无日期", "充值", "+1"]]) == (None, None), "解析不出日期 → (None, None)"
    print("⑧ vas_ledger_dates: 覆盖区间纯函数(不补零写法/空/脏行都对) ✓")


class FakeLoc:
    """⚠️ 只对 [data-hm-more] 选择器触发"翻页"(早期的假件对着任何 locator 都点,
    连「消费记录」那一下都当成翻页 → 表格提前被换掉, 测试自己骗自己)。"""

    def __init__(self, page, sel=""):
        self.page, self.sel = page, sel
        self.first = self

    def count(self):
        if "data-hm-more" in self.sel:
            return 1 if self.page.more_btn else 0
        return 1

    def click(self, timeout=None):
        if "data-hm-more" in self.sel:
            self.page.more_clicks += 1
            if self.page.more_rows:
                self.page.rows = self.page.more_rows      # 翻页后表格换成本页内容
                self.page.more_rows = None                # 只有一页可翻


class FakeFrame:
    def __init__(self, page):
        self.page = page

    def evaluate(self, js, arg=None):
        return self.page.evaluate(js, arg)

    def locator(self, sel):
        return FakeLoc(self.page, sel)


class FakeVasPage:
    """增值服务页假件: rows 之外还能给「加载更多」后的 more_rows / 探测结果。"""

    def __init__(self, rows, more_btn=False, more_rows=None, table_found=True):
        self.rows = rows
        self.more_btn = more_btn
        self.more_rows = more_rows
        self.table_found = table_found
        self.more_clicks = 0
        self._frames = [FakeFrame(self)]

    @property
    def frames(self):
        return self._frames

    def locator(self, sel):
        return FakeLoc(self, sel)

    def goto(self, url, **kw):
        return None

    def close(self):
        return None

    def evaluate(self, js, arg=None):
        js = js or ""
        if "data-hm-rec" in js:                       # 点「消费记录」
            return 1
        if "data-hm-more" in js:                      # 找「加载更多」(探测)
            return 1 if self.more_btn else 0
        if "tbody tr" in js:                          # _vas_rows
            return [self.rows] if (self.rows and self.table_found) else []
        if "table').length" in js:                    # 表格出现探测
            return 1 if self.table_found else 0
        if "out.inputs" in js:                        # _vas_probe_controls
            return {"inputs": ["日期 ⟨roo-input⟩"], "clicks": ["加载更多 ⟨a⟩"], "pager": []}
        if "document.body" in js:                     # 页面文本(等「消费记录」)
            return "消费记录 时间 消费类型 消费金额 余额 操作"
        return 0


class Log:
    def __init__(self):
        self.lines = []

    def log(self, msg, level="info"):
        self.lines.append((level, msg))



def main():
    p = T.parse_vas_ledger
    # ① 有充值 → 取到 + 余额
    r = p(ROWS, "2026-10-05")
    assert r["GK"] == 400.0 and r["balance"] == 1390.13 and r["hits"] == 1, r
    print("① 有充值 → GK=400.0, 余额=1390.13 ✓")

    # ② 只有消费的日子 → 留空(关键: 不能把 -246.49 当充值)
    r2 = p(ROWS, "2026-10-07")
    assert r2["GK"] is None and r2["hits"] == 0, f"只有消费必须留空, 实得 {r2}"
    print("② 只有推广消费 → GK=None(不臆造, 不拿消费额冒充) ✓")

    # ③ 同日多笔充值 → 累加
    r3 = p(ROWS + [['2026-10-05 18:00:00', '现金充值', '+100.00', '1490.13', '_']], "2026-10-05")
    assert r3["GK"] == 500.0 and r3["hits"] == 2, r3
    print("③ 同日两笔充值 → 400+100=500 累加 ✓")

    # ④ 脏数据不崩
    assert p(None, "2026-10-05")["GK"] is None
    assert p([None, [], ['x'], ['2026-10-05', '充值', 'abc']], "2026-10-05")["GK"] is None
    print("④ 空表/脏行不崩 ✓")

    # ⑤ 源码守卫: 充值来自 vas 页 + 用独立标签页(不破坏 stats frame) + 调用点在逐店流程里
    src = inspect.getsource(T.TaobaoAdapter._read_vas_ledger)
    assert "VAS_URL" in src and "消费记录" in src, "必须走 vas 页的消费记录"
    assert "ctx.new_page()" in src and "vp.close()" in src, "必须用独立标签页并关掉"
    assert "parse_vas_ledger" in src, "要复用纯函数解析"
    cps = inspect.getsource(T.TaobaoAdapter.capture_per_store)
    assert "_read_vas_ledger(sid, date)" in cps, "逐店里要读充值"
    assert "[充值]" in cps, "要有充值日志(便于真机核对)"
    assert T.VAS_URL.endswith("/vas#app.shop.vas"), T.VAS_URL
    print("⑤ 源码守卫: vas 页 + 独立标签页 + 逐店调用 ✓")

    # ── 2026-10-15 任务 J: 日期覆盖(根因三实锤: 表读到了、窗口不含目标日) ──
    _vas_ledger_cases()

    print("⑥ 源码守卫: 充值失败分支带表格覆盖证据 + 翻页/筛选探测 ✓")
    print("\n✅ 淘宝充值(GK) 全绿")


if __name__ == "__main__":
    main()
