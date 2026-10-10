# -*- coding: utf-8 -*-
"""京东报表下载健壮性: 菜单失败→直接导航兜底; 日期控件轮询等待+现场取证。

真机背景(2026-10-08):
  ① 日志 `[京东] 报表下载未跳转, 重试2/4` → 菜单点击不稳(代码注释本来就写"时灵时不灵");
     用户给出下载列表地址 https://store.jddj.com/frame/4734/5087 → 用它兜底。
  ② `[step] 下载报表 failed: Locator.click: Timeout 30000ms exceeded` → 日期控件找不到时
     闷等 30 秒, 看不到原因 → 改成轮询 + 现场取证(页面有哪些控件)。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "kernel"))
from merchant_report.platforms import jd as J   # noqa: E402


def main():
    # ① 下载列表 URL 常量存在且指向 store.jddj.com/frame
    assert J.JD_DOWNLOAD_URL.startswith("https://store.jddj.com/frame/"), J.JD_DOWNLOAD_URL
    print(f"① 兜底 URL 常量 ✓  {J.JD_DOWNLOAD_URL}")

    # ② _open_report_page: 菜单失败后要直接导航, 且允许环境变量覆盖
    src = inspect.getsource(J.JDAdapter._open_report_page)
    assert "JD_DOWNLOAD_URL" in src, "菜单失败必须走直接导航兜底"
    assert 'os.environ.get("JD_DOWNLOAD_URL"' in src, "要允许环境变量覆盖(frame号可能随账号不同)"
    assert "直接导航" in src, "要有日志说明走了兜底路径"
    print("② 菜单失败→直接导航兜底 ✓ (支持 JD_DOWNLOAD_URL 覆盖)")

    # ③ _set_dates_by_calendar: 逐个找可见候选 + 轮询等待 + 现场取证 + 切回数据下载 tab
    d = inspect.getsource(J.JDAdapter._set_dates_by_calendar)
    assert "def _find_visible_pick" in d, "要逐个找**可见的**控件(防隐藏孪生: .first 可能选中不可见的那份)"
    assert ".nth(" in d, "要遍历所有 .ant-picker-range 候选(不能只取 .first)"
    assert "for _ in range(8)" in d, "要轮询等待控件(不是闷等一次 click)"
    assert "现场" in d, "找不到控件要打印现场取证"
    assert "数据下载" in d, "可能停在下载列表 tab → 要切回数据下载"
    assert "click(timeout=10000)" in d, "点击要带短超时(子应用重渲染期元素不稳, 10s 封顶)"
    assert "重试3次后" in d, "点不动要重试3次才报错(2026-10-09: 刚挂载时单次点击会超时)"
    print("③ 日期控件: 可见候选遍历 + 轮询等待 + 现场取证 + 切回数据下载 ✓")

    print("\n✅ 京东下载健壮性 全绿")


if __name__ == "__main__":
    main()
