# -*- coding: utf-8 -*-
"""前端「一键跑所有平台」源码守卫(tsx 没单测框架, 用源码断言兜底)。"""
import pathlib


def main():
    p = pathlib.Path(__file__).resolve().parent.parent / "src" / "App.tsx"
    src = p.read_text(encoding="utf-8")
    assert "▶▶ 一键跑所有平台" in src, "按钮文案必须在(用户认这个)"
    assert "async function startAll()" in src and 'await launch("taobao", false)' in src, "startAll 要从淘宝开始"
    assert 'allQueue.current = ["meituan", "meituan_gj", "jd"]' in src, "顺序必须是 淘宝→美团→美团管家→京东"
    assert "一键模式 第" in src, "要有 第N/4 个平台的进度提示"
    assert "一键跑所有平台 · 汇总" in src, "结尾要有汇总"
    assert "allTotal.current > 0" in src and "allQueue.current.shift()" in src, "result 回调里要自动续跑下一个"
    assert "某个平台失败也继续跑下一个" in src, "失败不停(用户口径: 跑一遍看全部问题)"
    # 单平台老路径不能被破坏
    assert "async function start(skipBrowser = false)" in src, "单平台入口必须保留"
    assert 'template: tpl' in src and "(merchant as any)?.[platform]?.template" in src, "要按**各平台自己的模板**跑"
    # 门店明细(设置→商家管理 上传, 2026-10-09): 选择/清除 + 传参给内核 + 壳转发
    assert "store_details?: string" in src, "Merchant 类型要有 store_details"
    assert "pickStoreDetails" in src and "选择门店明细表" in src, "设置页要有门店明细选择"
    assert 'storeDetails: (merchant as any)?.store_details ?? ""' in src, "任务启动要传 storeDetails"
    rust = (p.parent.parent / "src-tauri" / "src" / "py_runner.rs").read_text(encoding="utf-8")
    assert "store_details: String," in rust and '"--store-details"' in rust, "壳要把门店明细转给内核"
    print("✅ 一键跑所有平台 · 前端守卫 全绿")


if __name__ == "__main__":
    main()
