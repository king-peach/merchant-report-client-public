# -*- coding: utf-8 -*-
"""淘宝充值(GL): 增值服务页「消费记录」→ 当日充值金额。

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


def main():
    p = T.parse_vas_ledger
    # ① 有充值 → 取到 + 余额
    r = p(ROWS, "2026-10-05")
    assert r["GL"] == 400.0 and r["balance"] == 1390.13 and r["hits"] == 1, r
    print("① 有充值 → GL=400.0, 余额=1390.13 ✓")

    # ② 只有消费的日子 → 留空(关键: 不能把 -246.49 当充值)
    r2 = p(ROWS, "2026-10-07")
    assert r2["GL"] is None and r2["hits"] == 0, f"只有消费必须留空, 实得 {r2}"
    print("② 只有推广消费 → GL=None(不臆造, 不拿消费额冒充) ✓")

    # ③ 同日多笔充值 → 累加
    r3 = p(ROWS + [['2026-10-05 18:00:00', '现金充值', '+100.00', '1490.13', '_']], "2026-10-05")
    assert r3["GL"] == 500.0 and r3["hits"] == 2, r3
    print("③ 同日两笔充值 → 400+100=500 累加 ✓")

    # ④ 脏数据不崩
    assert p(None, "2026-10-05")["GL"] is None
    assert p([None, [], ['x'], ['2026-10-05', '充值', 'abc']], "2026-10-05")["GL"] is None
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
    print("\n✅ 淘宝充值(GL) 全绿")


if __name__ == "__main__":
    main()
