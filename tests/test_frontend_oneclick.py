# -*- coding: utf-8 -*-
"""前端「一键跑所有平台」源码守卫(tsx 没单测框架, 用源码断言兜底)。

2026-10-12 需求 A1: 队列改由《门店明细》驱动 —— 明细里有门店数据的平台才跑/才需要模板。
真实样例表只有 淘宝/美团/管家(没有京东), 旧实现却要求 4 个平台全配模板才允许开跑。
未配置/探测失败/浏览器 dev → 回退旧行为(原 4 平台 + 原预检)。
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
    assert "PLATFORM_ORDER.filter((p) => plats.includes(p))" in src, "队列=明细里有数据的平台(保序)"
    assert "allQueue.current = order.slice(1)" in src and "await launch(order[0], false)" in src, \
        "要从队列第一个平台开始, 其余进队列"
    assert "一键模式 第" in src, "要有 第N/4 个平台的进度提示"
    assert "一键跑所有平台 · 汇总" in src, "结尾要有汇总"
    assert "allTotal.current > 0" in src and "allQueue.current.shift()" in src, "result 回调里要自动续跑下一个"
    assert "某个平台失败也继续跑下一个" in src, "失败不停(用户口径: 跑一遍看全部问题)"
    # A1: 平台探测走壳命令(内核读 xlsx), 失败回退旧行为
    assert 'await invoke("read_store_platforms", { storeDetails })' in src, \
        "要调壳命令 read_store_platforms(参数名沿用 camelCase 约定)"
    assert "门店明细里没读到任何有数据的平台" in src and "回退为全部 4 个平台" in src, \
        "探测失败/为空要回退旧行为并日志提示"
    assert "门店明细里有这些平台的门店数据，但还没为它配置模板" in src, "缺模板提示要指向门店明细口径"
    assert "没配置则和以前一样" in src, "按钮 title 要说明回退行为"
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
