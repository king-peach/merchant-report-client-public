# -*- coding: utf-8 -*-
"""前端「一键跑所有平台」源码守卫(tsx 没单测框架, 用源码断言兜底)。

2026-10-13 用户口径: **所有平台都要跑** —— 队列 = 4 个平台全跑(淘宝→美团→管家→京东)。
《门店明细》不决定队列, 只决定**切店方式**(有编号的平台走「搜索ID直选」, 其余按旧逻辑)。
历史: 10-12 曾按明细过滤队列(京东空组被整条滤掉 → 用户报"京东没跑"), 当日撤销。
"""
import pathlib


def main():
    p = pathlib.Path(__file__).resolve().parent.parent / "src" / "App.tsx"
    src = p.read_text(encoding="utf-8")
    assert "▶▶ 一键跑所有平台" in src, "按钮文案必须在(用户认这个)"
    assert "async function startAll()" in src, "一键入口必须在"
    # 相对顺序仍是 淘宝→美团→管家→京东, 且起点=队列第一个平台(不再是写死的淘宝)
    assert 'const PLATFORM_ORDER = ["taobao", "meituan", "meituan_gj", "jd"]' in src, \
        "平台相对顺序必须保持不变"
    assert "const order = PLATFORM_ORDER;" in src, "队列=全部 4 个平台(用户口径: 所有平台都要跑)"
    assert "plats.includes" not in src, "队列不许再按门店明细过滤(明细只决定切店方式)"
    assert "4 个平台全跑" in src, "一键日志/按钮说明要明示 4 平台全跑"
    assert "allQueue.current = order.slice(1)" in src and "await launch(order[0], false)" in src, \
        "要从队列第一个平台开始, 其余进队列"
    assert "一键模式 第" in src, "要有 第N/4 个平台的进度提示"
    assert "一键跑所有平台 · 汇总" in src, "结尾要有汇总"
    assert "allTotal.current > 0" in src and "allQueue.current.shift()" in src, "result 回调里要自动续跑下一个"
    assert "某个平台失败也继续跑下一个" in src, "失败不停(用户口径: 跑一遍看全部问题)"
    # 明细只决定"切店方式"(有编号走搜索直选/没有走旧逻辑) —— 该能力在平台模块里(各自守卫测);
    # 这里钉: 队列不再依赖明细探测 + 4 平台模板预检保持 + 按钮说明更新
    assert "请先在「设置 → 商家管理」为这些平台各选一个模板" in src, "缺模板要拦下并给出路"
    assert "切店方式自动按门店明细优化" in src, "按钮 title 要说明明细的作用范围(只管切店方式)"
    # 单平台老路径不能被破坏
    assert "async function start(skipBrowser = false)" in src, "单平台入口必须保留"
    assert 'template: tpl' in src and "(merchant as any)?.[platform]?.template" in src, "要按**各平台自己的模板**跑"
    # 门店明细(设置→商家管理 上传, 2026-10-09): 选择/清除 + 传参给内核 + 壳转发
    assert "store_details?: string" in src, "Merchant 类型要有 store_details"
    assert "pickStoreDetails" in src and "选择门店明细表" in src, "设置页要有门店明细选择"
    assert 'storeDetails: (merchant as any)?.store_details ?? ""' in src, "任务启动要传 storeDetails"
    rust = (p.parent.parent / "src-tauri" / "src" / "py_runner.rs").read_text(encoding="utf-8")
    assert "store_details: String," in rust and '"--store-details"' in rust, "壳要把门店明细转给内核"
    # A1: 壳侧探测命令 + main.rs 注册
    assert "pub fn read_store_platforms(" in rust and '"--platforms"' in rust, \
        "壳要有 read_store_platforms 并转调内核 --platforms"
    assert "resolve_kernel_dir(" in rust, "kernel 目录发现要提取成公共函数(与 run_task 一致)"
    main_rs = (p.parent.parent / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    assert "py_runner::read_store_platforms" in main_rs, "main.rs 要注册 read_store_platforms"

    # ---------- 时间段逐日串行(2026-10-14 用户口径: 区间跨多天 → 按天串行跑单日一键全平台) ----------
    assert "function clampRange(" in src, "区间要收敛成升序天列表(clamp 到昨天)"
    assert "if (from > end) return [];" in src, "整段都在未来(clamp 后为空)要能识别"
    assert "const end = to > yesterday() ? yesterday() : to;" in src, "区间终点要 clamp 到昨天(不跑今天/未来)"
    assert "起始日期晚于结束日期" in src, "起点晚于终点要报错"
    assert "没有可跑的天" in src, "整段在未来要报错提示"
    assert "已收到昨天" in src, "clamp 发生时要有日志让用户可感知"
    # 天循环状态
    assert "const allDays = useRef<string[]>([])" in src, "要有剩余天队列"
    assert "const allDayTotal = useRef(0)" in src and "const allDayDone = useRef(0)" in src, \
        "要有 第 k/N 天 的进度计数"
    assert 'useRef<OneClickResult[]>([])' in src, "结果要按 {day, platform, ok} 记账(汇总矩阵用)"
    assert "const allMode = useRef<\"single\" | \"range\">" in src, "要区分单日/时间段两种模式"
    # 入口分流: 单日路径不变, 多日才进时间段模式
    assert "const days = clampRange(dateFrom, dateTo);" in src, "startAll 要先算出天列表"
    assert "if (days.length === 1) {" in src, "单日区间必须走原单日路径"
    assert "await launch(order[0], false);" in src, "单日区间必须仍走原来的一键发起(不传当天)"
    assert "allMode.current = \"range\";" in src, "多日要进时间段模式"
    assert "▶▶ 时间段模式：共 ${days.length} 天" in src, "时间段模式要有开工日志(天数可感知)"
    # 天末装填: 平台队列跑空 → 装填下一天重新开跑
    assert "const nextDay = allDays.current.shift();" in src, "一天跑完要取下一day"
    assert "allQueue.current = PLATFORM_ORDER.slice(1);" in src, "下一天要重新装填 4 平台队列"
    assert "void launch(PLATFORM_ORDER[0], false, nextDay);" in src, "下一天要从第一个平台重新开跑(并带上当天)"
    assert "天完成：" in src and "（淘宝/美团/管家/京东）" in src, "每天要有一条 第k/N天完成 ✅✅❌✅ 的日志"
    # 传参: 时间段模式 date/dateFrom/dateTo 全用当天
    assert "async function launch(platform: string, skipBrowser = false, day = \"\")" in src, \
        "launch 要多一个当天参数(最小侵入)"
    assert "const runDay = day || allDayCurrent.current || dateTo;" in src, "当天值要有兜底"
    assert "date: allMode.current === \"range\" ? runDay : dateTo," in src, "时间段模式 date 必须是当天"
    assert "dateFrom: allMode.current === \"range\" ? runDay : dateFrom," in src, "dateFrom 必须是当天"
    assert "dateTo: allMode.current === \"range\" ? runDay : dateTo," in src, "dateTo 必须是当天"
    assert "await launch(order[0], false, days[0]);" in src, "时间段模式首日要带着当天发起"
    assert "时间段的 第" in src or "[第 ${allDayDone.current + 1}/${allDayTotal.current} 天] 第" in src, \
        "天循环里每条平台启动日志要带 [第 k/N 天] 前缀"
    # E3 汇总
    assert "━━━ 时间段跑完 · 汇总 ━━━" in src, "时间段模式要有独立汇总标题"
    assert "function dayMarks(" in src and "天 × " in src and "任务：" in src, "汇总要有 天×平台 矩阵 + 总计行"
    # E2 终止: 清空剩余天/平台队列 + 不再自动续跑
    assert "const aborted = useRef(false)" in src, "要有终止标志"
    assert "!aborted.current" in src, "result 回调在终止后不得再启动新任务"
    assert "allQueue.current = []; allDays.current = [];" in src, "终止要清空剩余平台队列与剩余天"
    assert "已终止：完成 " in src, "终止后如有已完成结果要给简短汇总"
    # E4 UI 文案
    assert "按天串行" in src and "每天跑全部 4 平台" in src, "区间提示要说明按天串行 + 每天全平台"
    print("✅ 一键跑所有平台(门店明细驱动) · 前端/壳守卫 全绿")


if __name__ == "__main__":
    main()
