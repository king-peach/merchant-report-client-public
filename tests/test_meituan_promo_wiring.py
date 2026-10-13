# -*- coding: utf-8 -*-
"""推广账户流水接线: 按店按天取 → 写 CE(消耗)/CJ(充值); URL 必须是固定基址。

2026-10-08: 用户在两张截图里给出推广充值位置(美团=门店推广→消费记录, URL 内层 /ad/v1/pc),
并明确提醒"带 bsid/device_uuid/time 的整串 URL 不能写死" → 这里把基址断言住。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report import orchestrator as orc                              # noqa: E402
from merchant_report.platforms import meituan as mt                           # noqa: E402


def main():
    src = inspect.getsource(orc)
    assert "promo_by_sheet = {}" in src, \
        "promo_by_sheet 必须有函数级初始化(否则商圈块没跑时, 写入处会未绑定报错)"
    assert 'getattr(_ad, "capture_promo_ledger", None)' in src, \
        "必须做平台判断(淘宝适配器没有该方法, 直接调会每店抛属性错误)"
    assert "_ledger_fn(_sid, _d)" in src, "要按店按天取流水"
    assert "promo_by_sheet.setdefault(" in src, "结果要按 Sheet+日期 存"
    # 2026-10-14 (B1): 门店表与主表**两个写入点**都必须走 _promo_vals(新口径), 不许各自拼 CE/CJ
    assert "_ws2.update(mt_map.validate_write_set(_promo_vals(d, _sn)))" in src, "门店 Sheet 的推广列要按店按日写"
    assert "ws.update(mt_map.validate_write_set(_promo_vals(d)))" in src, "主表同样要写推广列(同一口径)"
    assert '_ws2["CE"] = _pl["consume"]' not in src, "旧写法(直取 consume 当 CE)必须已删除"
    print("✅ 接线: 按店按天取流水 → promo_by_sheet → CE/CJ 写入(带平台判断)")
    assert "对账页" in src and "CF/CG" in src, "主表收尾文案要写明 CE 来源与 CF/CG 口径"
    print("✅ 主表/门店两个写入点共用 _promo_vals(CE/CJ 流水 + CF/CG 概览)")

    A = mt.MeituanAdapter
    assert callable(A.parse_ledger) and callable(A.capture_promo_ledger)
    assert A.PROMO_LEDGER_URL == "https://waimaieapp.meituan.com/ad/v1/pc#/account?scrollConsume=true", \
        f"流水页必须是固定基址+hash路由(不带会话参数): {A.PROMO_LEDGER_URL}"
    for bad in ("bsid", "device_uuid", "time=", "topOrigin"):
        assert bad not in A.PROMO_LEDGER_URL, f"URL 不该带会话参数 {bad}(用户明确提醒不能写死)"
    print("✅ URL 固定基址, 不含 bsid/device_uuid/time 等会话参数")

    # ── 对账页解析(2026-10-10 真机行数据; 用户指路: 历史日期走对账页自定义时间) ──
    BILL = [
        ["日期", "类型", "金额 (元)", "现有余额", "状态", "交易号"],
        ["2026-10-10 01:01:30", "推广账户自动充值", "+200.00", "￥229.72", "交易成功", "a"],
        ["2026-10-10 00:23:41", "推广订单扣款", "-138.82", "￥29.72", "交易成功", "b"],
        ["2026-10-09 00:23:51", "推广订单扣款", "-140.55", "￥168.54", "交易成功", "g"],
        ["2026-10-07 14:54:46", "推广账户自动充值", "+200.00", "￥346.25", "交易成功", "c"],
        ["2026-10-06 20:51:05", "推广账户自动充值", "+200.00", "￥396.25", "交易成功", "d"],
        ["2026-10-06 00:23:17", "推广订单扣款", "-191.39", "￥196.25", "交易成功", "e"],
    ]
    r = A.parse_bill_ledger(BILL, "2026-10-06")
    assert r["recharge"] == 200.0 and r["balance"] == 396.25 and r["n_recharge"] == 1, r
    # B1 核心口径: CE = 当日「推广订单扣款」**绝对值合计**(负数取正, 不是余额变动净额)
    assert r["consume"] == 191.39 and r["n_consume"] == 1, r
    r2 = A.parse_bill_ledger(BILL, "2026-10-09")       # 只有扣款的日子 → 充值留空(不拿扣款冒充)
    assert r2["recharge"] is None and r2["n_recharge"] == 0, r2
    assert r2["consume"] == 140.55, r2                 # 同日无充值也要出 CE
    r3 = A.parse_bill_ledger(BILL + [["2026-10-06 22:00:00", "推广账户自动充值", "+100.00", "￥496.25", "交易成功", "f"]],
                             "2026-10-06")
    assert r3["recharge"] == 300.0 and r3["n_recharge"] == 2, r3
    # B1 验收基线(规格书 §6): 高桥店 2026-09-19 当日「推广订单扣款」-151.58 → CE=151.58
    acc = A.parse_bill_ledger(
        [["日期", "类型", "金额 (元)", "现有余额", "状态", "交易号"],
         ["2026-09-19 00:20:11", "推广订单扣款", "-151.58", "￥101.00", "交易成功", "z"]],
        "2026-09-19")
    assert acc["consume"] == 151.58, acc
    # 同日多笔扣款 → 绝对值累加(不是互相抵消)
    acc2 = A.parse_bill_ledger(
        [["日期", "类型", "金额 (元)", "现有余额", "状态", "交易号"],
         ["2026-09-19 00:20:11", "推广订单扣款", "-100.00", "￥101.00", "交易成功", "z1"],
         ["2026-09-19 12:00:00", "推广订单扣款", "-51.58", "￥49.42", "交易成功", "z2"]],
        "2026-09-19")
    assert acc2["consume"] == 151.58 and acc2["n_consume"] == 2, acc2
    assert A.parse_bill_ledger(None, "2026-10-06")["recharge"] is None
    assert A.parse_bill_ledger([None, [], ["x"], ["2026-10-06", "充值", "abc"]], "2026-10-06")["recharge"] is None
    assert "billReconciliation.html#/account-flow" in A.BILL_RECON_URL and "wmPoiId=" in A.BILL_RECON_URL
    print("✅ 对账页解析: CE=扣款绝对值合计(9/19=-151.58→151.58, 多笔累加) / 充值累加 / 脏数据不崩")

    # ── 对账页接线守卫(2026-10-10: 历史日期不再翻页找) ──
    src_bill = (inspect.getsource(A._read_bill_ledger) + inspect.getsource(A._set_bill_inputs)
                + inspect.getsource(A.capture_promo_ledger))
    assert "BILL_RECON_URL" in src_bill, "对账页 URL 要接上"
    assert "推广费流水记录" in src_bill and "自定义" in src_bill, "要走对账页的推广费流水 tab + 自定义时间"
    assert "ControlOrMeta" in src_bill, "日期用键盘全选替换(受控输入不能只赋 value)"
    assert "交易号" in src_bill, "tab 就绪校验要盯「交易号」表头(余额 tab 末列是「操作」一行)"
    assert "失焦" in src_bill, "必须保留失焦提交(仅回车不刷新表格)"
    assert "对账页" in inspect.getsource(A.capture_promo_ledger), "日志要写明来源=对账页(可 grep 复验)"
    assert "仅供参考" in inspect.getsource(A.capture_promo_ledger), \
        "旧账户页「推广消费」必须标注仅供参考(规格书坑清单 8: 不得再作 CE)"
    print("✅ 对账页接线: URL / tab / 自定义 / 键盘替换 / 失焦提交 / 来源标注 守卫通过")

    print("✅ 推广流水接线单测全部通过")


if __name__ == "__main__":
    main()
