# -*- coding: utf-8 -*-
"""淘宝下载兜底单测: ①下载事件计数 ②文件下到浏览器默认目录时能找回(不移动原文件)。

背景(2026-09-28 Windows): 报「目录 D:\\prod\\商家报表助手\\reports 里缺「全部、爆品团」报表
（已有: 无）」—— 一个文件都没落盘。要区分「平台慢」和「我们收不到」, 必须知道下载事件
触发了几次; 并给一条兜底: CDP 的 Browser.setDownloadBehavior 被忽略时文件会落到浏览器
默认下载目录, 那里能找回来。
"""
import os
import pathlib
import shutil
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms.taobao import TaobaoAdapter                    # noqa: E402


def make_adapter():
    ad = TaobaoAdapter.__new__(TaobaoAdapter)      # 不走 __init__(不需要浏览器)
    logs = []
    ad.sink = type("S", (), {"log": lambda self, m, level="info": logs.append((level, m))})()
    ad._logs = logs
    ad._dl_events = 0
    ad._save_download = lambda d, dl_dir: None      # 避免真去落盘
    return ad


def main():
    # ① 下载事件计数
    ad = make_adapter()
    h = ad._on_download(pathlib.Path("/tmp"))
    for _ in range(3):
        h(object())
    assert ad._dl_events == 3, ad._dl_events
    print("✅ 下载事件计数: 收到 3 次记 3 次(超时时用它区分「没触发」和「被丢弃」)")

    # ② 找回下到浏览器默认目录的文件
    home = tempfile.mkdtemp(prefix="fake_home_")
    dl = os.path.join(home, "Downloads")
    reports = os.path.join(home, "reports")
    os.makedirs(dl)
    os.makedirs(reports)
    big = os.path.join(dl, "门店下载_20260927至20260927_全部门店_1111111111_20260928093830682.xlsx")
    small = os.path.join(dl, "门店下载_空文件.xlsx")
    old = os.path.join(dl, "门店下载_老文件.xlsx")
    with open(big, "wb") as f:
        f.write(b"x" * 5000)                 # >1KB
    with open(small, "wb") as f:
        f.write(b"x" * 10)                   # <1KB → 不要
    with open(old, "wb") as f:
        f.write(b"x" * 5000)
    os.utime(old, (time.time() - 3600 * 5, time.time() - 3600 * 5))   # 5 小时前 → 不要

    _real_expand = os.path.expanduser
    os.path.expanduser = lambda p: home if p == "~" else _real_expand(p)
    try:
        ad = make_adapter()
        got = ad._recover_stray_downloads(reports)
    finally:
        os.path.expanduser = _real_expand

    names = [os.path.basename(x) for x in got]
    assert names == [os.path.basename(big)], names
    assert os.path.exists(os.path.join(reports, os.path.basename(big))), "应复制进报表目录"
    assert os.path.exists(big), "原文件必须保留(只复制不移动)"
    assert len(os.listdir(reports)) == 1
    print("✅ 找回: 只捞 >1KB 且 40 分钟内的门店下载文件, 复制进报表目录且不移动原文件")

    # ③ 报表目录里已有同名文件 → 不重复复制
    ad = make_adapter()
    os.path.expanduser = lambda p: home if p == "~" else _real_expand
    try:
        got2 = ad._recover_stray_downloads(reports)
    finally:
        os.path.expanduser = _real_expand
    assert got2 == [], got2
    print("✅ 幂等: 目标目录已有同名文件时不重复复制")

    shutil.rmtree(home, ignore_errors=True)
    print("✅ 淘宝下载兜底单测全部通过")


if __name__ == "__main__":
    main()
