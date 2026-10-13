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
    print("✅ 一键跑所有平台(门店明细驱动) · 前端/壳守卫 全绿")


if __name__ == "__main__":
    main()
