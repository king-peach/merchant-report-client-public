# -*- coding: utf-8 -*-
"""商圈TOP 逐店**逐日**写入(任务 J·B)单测 —— 去掉 `d == dates[-1]` 门槛。

真机背景(2026-10-14 真机输出-1014.xlsx): 区间 09-10~09-12 三天里, **只有最后一天的行**
(以及单日/单店视角那条路径)可能拿到 TOP; 更根本的是采集侧 18/18「日期未确认」→ 全空。
采集侧交互已在 test_meituan_flow_date.py 钉住; 本文件钉**编排侧**:

  ① 采集循环 = 每家 matched 店 × **每个日期**(不再只采 dates[-1]);
  ② 写入侧按 `(Sheet, 日期)` 取 —— 多日区间里**每天的行都要写**, 该日缺 → 该列不写(留空),
     **绝不用别天的值顶替**(那正是"数字看着正常但日期是错的"这类最危险的错法);
  ③ 单日区间行为不变(等价于旧行为, 不回归);
  ④ 过期文案清理: 不再有「全部门店模式不采集」这类把 TOP 当"仅单店可得"的说法。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report import orchestrator as orc                                # noqa: E402
from merchant_report.platforms import meituan_mapping as mm                    # noqa: E402

ORC = inspect.getsource(orc)

# capture_circle 的返回形状(真机字段; 见 meituan.py capture_circle)
CAP = {"main": {"整体": {"曝光": 1000, "进店": 100, "进店转化率": 0.1, "下单": 40, "下单转化率": 0.4},
                "新客": {"曝光": 800, "进店": 70, "下单转化率": 0.35},
                "老客": {"曝光": 200, "进店": 30, "下单转化率": 0.6}},
       "bao": {"整体": {"曝光": 500, "进店": 50, "进店转化率": 0.1, "下单": 20, "下单转化率": 0.4},
               "新客": {"曝光": 400, "进店": 35, "下单转化率": 0.33},
               "老客": {"曝光": 100, "进店": 15, "下单转化率": 0.6}},
       "bench": "商圈同行前10%均值"}


def sim_orchestrator(dates, capture_map):
    """复刻编排侧的两段真实逻辑(采集循环 + 写入侧取值), 供行为断言用。

    capture_map: {(店名, 日期): capture 或 None} —— None 模拟该日该店没采到。
    返回 {日期: {模板列: 值}}(只含被写入的列)。
    """
    circle_by_sheet = {}
    matched = [("测试店", "测试店", "keyword")]
    for _nm, _sn, _why in matched:                 # ← 与 orchestrator 同构: 店在外层, 日在内层
        for _d in dates:
            _capd = capture_map.get((_nm, _d))
            if _capd:
                circle_by_sheet[(_sn, _d)] = _capd
    out = {}
    for d in dates:                                # ← 写入侧: 逐日取, 无 dates[-1] 门槛
        ws = {}
        _c = circle_by_sheet.get(("测试店", d))
        if _c:
            _cw = mm.circle_write_set_all(_c)
            ws.update(_cw["main"])
            ws.update(_cw["bao"])
        out[d] = ws
    return out


def main():
    # ① 采集循环: 每家店 × 每个日期(源码契约 —— 防有人把循环改回"只采最后一天")
    assert "for _d in dates:" in ORC, "采集循环必须遍历每个日期"
    assert "circle_by_sheet[(_sn, _d)] = _capd" in ORC, "结果要按 (Sheet, 日期) 存"
    assert 'f"单门店视角 × {len(store_plan[\'matched\'])} 店 × {len(dates)} 天"' in ORC, \
        "step 开始文案要写明 店×天"
    assert "店×天取到商圈值" in ORC, "step 结果文案要写明 店×天"
    print("✅ ① 采集循环 = 每店 × 每天(circle_by_sheet[(Sheet, 日期)])")

    # ② 写入侧: 逐日取, 门槛已删
    assert "_c = circle_by_sheet.get((_sn, d))" in ORC, "写入侧必须按 (Sheet, 日期) 取"
    assert "circle_by_sheet.get((_sn, d)) if d == dates[-1]" not in ORC, "旧的 dates[-1] 门槛必须已删"
    assert 'if circle_by_sheet.get((_sn, dates[-1]))' not in ORC, "别把门槛换个写法藏回来"
    print("✅ ② 写入侧去掉 dates[-1] 门槛, 改为按 (Sheet, 日期) 取")

    # ③ 行为: 三日区间、每天都能采到 → **每天都写 TOP**(不再只有最后一天)
    days = ["2026-09-10", "2026-09-11", "2026-09-12"]
    got = sim_orchestrator(days, {(("测试店", d)): CAP for d in days})
    TOP_COLS = ("M", "P", "S", "V", "Y", "AY", "BB", "BE", "BH", "BK")
    for d in days:
        assert all(c in got[d] for c in TOP_COLS), (d, sorted(got[d]))
    assert got["2026-09-10"]["M"] == 1000 and got["2026-09-12"]["M"] == 1000
    print(f"✅ ③ 三日区间 → 三天**每天**都有 TOP 写入({len(TOP_COLS)} 列)")

    # ④ 行为: 中间那天采不到 → 该日 TOP 列**一个都不写**(留空), 且绝不拿邻天的值顶替
    cap2 = {(("测试店", d)): CAP for d in days}
    cap2[("测试店", "2026-09-11")] = None
    got2 = sim_orchestrator(days, cap2)
    assert not any(c in got2["2026-09-11"] for c in TOP_COLS), sorted(got2["2026-09-11"])
    assert got2["2026-09-10"]["M"] == 1000 and got2["2026-09-12"]["M"] == 1000
    print("✅ ④ 某天采不到 → 该日 TOP 列全不写(不拿邻天值顶替), 其它天照写")

    # ⑤ 行为: 单日区间 = 旧行为(等价, 不回归)
    one = sim_orchestrator(["2026-09-12"], {("测试店", "2026-09-12"): CAP})
    assert all(c in one["2026-09-12"] for c in TOP_COLS)
    assert mm.circle_write_set_all(CAP)["main"]["M"] == 1000
    print("✅ ⑤ 单日区间行为不变(只有那天, 照写)")

    # ⑥ 过期文案清理(用户点名 + 规格书 §1.2 佐证)
    mm_src = inspect.getsource(mm)
    assert "不再区分" in mm_src and "逐店逐日采集" in mm_src, "meituan_mapping 采集口径注释要更新"
    assert "全部门店模式不采集" not in mm_src, "「全部门店模式不采集」必须删除"
    assert "只填本店列" not in mm_src, "「只填本店列; 商圈TOP列不写」必须删除"
    assert "本店列与商圈TOP列均写" in mm_src, "要写明本店列与 TOP 列均写"
    assert "全部门店模式不采集" not in ORC, "orchestrator 注释同步"
    for bad in ("未采集(仅单门店视角可得", "全部门店模式或单店漏斗不可用时留空"):
        assert bad not in ORC, f"过期 summary 文案还在: {bad}"
    assert "未采到(逐店逐日单门店视角采集" in ORC, "summary 要写实际采集口径"
    print("✅ ⑥ 过期文案(全部门店模式不采集 / 仅单门店视角可得)已清理, summary 改为实际口径")

    # ⑦ 差异列仍禁写(不因 B 项被放松)
    cw = mm.circle_write_set_all(CAP)
    assert "N" not in cw["main"] and "Q" not in cw["main"], "差异列(N/Q 等)不能进写入集"
    print("✅ ⑦ 差异列仍禁写(不因 B 项放松)")

    print("\n✅ 商圈TOP 逐店逐日单测全部通过")


if __name__ == "__main__":
    main()
