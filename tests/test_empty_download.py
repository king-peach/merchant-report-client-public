# -*- coding: utf-8 -*-
"""空/坏报表文件守卫测试。

背景(2026-09-23 真机踩到): 平台"任务已提交但文件还没生成好"时会落地 **0 字节** .xlsx,
旧代码把 0 字节当成"下到了" → 用户看到的是「缺「全部」报表」这种完全误导的报错。

要求:
1. `broken_report_files()` 能把空文件/坏文件挑出来并给原因;
2. `TaobaoAdapter._save_download` 遇到空文件**丢弃并返回**(不当成有效下载), 让上层继续等;
3. `find_report_files` 对空文件不误判为有效报表。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kernel"))

from merchant_report.fill import broken_report_files, find_report_files          # noqa: E402
from merchant_report.platforms.taobao import TaobaoAdapter                        # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


class FakeSink:
    def __init__(self):
        self.logs = []

    def log(self, msg, level="info"):
        self.logs.append((level, msg))


class FakeDownload:
    """伪造 Playwright Download: 只实现 suggested_filename / save_as。"""

    def __init__(self, name, size, payload=b"PK\x03\x04fake-xlsx"):
        self.suggested_filename = name
        self._size = size
        self._payload = payload

    def save_as(self, path):
        with open(path, "wb") as f:
            if self._size > 0:
                f.write(self._payload * max(1, self._size // len(self._payload)))
        # 需要精确控制大小(模拟 0 字节 / 小文件)时, 直接截断
        if self._size == 0:
            open(path, "wb").close()


def main():
    print("【1】broken_report_files 能识别空文件 / 坏文件")
    with tempfile.TemporaryDirectory() as d:
        date = "2026-09-22"
        empty = os.path.join(d, f"门店下载_20260922至20260922_全部门店_111_20260923161225981.xlsx")
        open(empty, "wb").close()                                     # 0 字节(真机复现)
        junk = os.path.join(d, f"门店下载_20260922至20260922_全部门店_111_20260923161233278.xlsx")
        with open(junk, "wb") as f:
            f.write(b"not a zip at all" * 200)                        # 非 zip
        ok = os.path.join(d, f"门店下载_20260922至20260922_全部门店_111_20260923000000000.xlsx")
        with open(ok, "wb") as f:                                     # 名字对但不是报表(无 data 表)
            import openpyxl
            wb = openpyxl.Workbook()
            wb.save(f.name)

        broken = broken_report_files(d, date)
        names = [n for n, _ in broken]
        check("0 字节文件被识别", any("161225981" in n for n in names), str(names))
        check("非 zip 文件被识别", any("161233278" in n for n in names), str(names))
        check("原因里带字节数", any("字节" in w for _, w in broken), str(broken))
        check("没有 data 表的文件也算无效", any("000000000" in n for n in names), str(names))
        check("目录里 3 个文件全部被识别", len(names) == 3, str(names))
        all_f, bom_f = find_report_files(d, date)
        check("find_report_files 不把空文件当真报表", all_f is None and bom_f is None,
              f"all={all_f} bom={bom_f}")

    print("【2】_save_download 丢弃空文件")
    with tempfile.TemporaryDirectory() as d:
        ad = object.__new__(TaobaoAdapter)
        ad.sink = FakeSink()
        ad._save_download(FakeDownload("空报表.xlsx", 0), d)
        check("空文件没有留在目录里", not os.path.exists(os.path.join(d, "空报表.xlsx")),
              str(os.listdir(d)))
        check("打了 warn 日志", any(lv == "warn" and "空" in m for lv, m in ad.sink.logs),
              str(ad.sink.logs))

        ad2 = object.__new__(TaobaoAdapter)
        ad2.sink = FakeSink()
        ad2._save_download(FakeDownload("正常报表.xlsx", 4096), d)
        p = os.path.join(d, "正常报表.xlsx")
        check("正常大小文件保留", os.path.exists(p) and os.path.getsize(p) >= 1024,
              str(os.path.getsize(p) if os.path.exists(p) else "缺失"))
        check("日志带文件大小", any("已保存下载" in m and "KB" in m for _, m in ad2.sink.logs),
              str(ad2.sink.logs))

        print("【3】重名自动加序号(回归)")
        ad3 = object.__new__(TaobaoAdapter)
        ad3.sink = FakeSink()
        ad3._save_download(FakeDownload("正常报表.xlsx", 4096), d)
        check("第二个同名文件变成 _1", os.path.exists(os.path.join(d, "正常报表_1.xlsx")),
              str(sorted(os.listdir(d))))

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
