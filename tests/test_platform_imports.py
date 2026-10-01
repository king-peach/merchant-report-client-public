# -*- coding: utf-8 -*-
"""platforms/ 子包内的相对导入层级检查 —— 治「改了才知道」的老坑。

坑的形状(2026-09-23 真机踩到, 报错是 `cannot import name 'sheet_match' from
'merchant_report.platforms'`): 在 `platforms/*.py` 里写 `from . import sheet_match`,
但 sheet_match 是**上一层**的模块 → 必须写 `from .. import sheet_match`。
这类错误只在该代码路径被执行时才炸(静态检查、未跑到的分支全看不出来),
所以用本测试把「所有 platforms/*.py 里引用上层模块必须用 `..`」钉死。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KERNEL = os.path.join(HERE, "..", "kernel")
PLATFORMS = os.path.join(KERNEL, "merchant_report", "platforms")
TOP = os.path.join(KERNEL, "merchant_report")

FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    # 上层(merchant_report/)的模块名 = 单点导入时必须禁止的目标
    upper = {f[:-3] for f in os.listdir(TOP) if f.endswith(".py") and f != "__init__.py"}
    here = {f[:-3] for f in os.listdir(PLATFORMS) if f.endswith(".py") and f != "__init__.py"}
    print(f"【1】platforms/ 与上层模块清单: 上层 {len(upper)} 个, 本层 {len(here)} 个")
    check("sheet_match 属于上层(必须 ..)", "sheet_match" in upper and "sheet_match" not in here)

    print("【2】platforms/*.py 里 `from . import X` 不得引用上层模块")
    bad = []
    for f in sorted(os.listdir(PLATFORMS)):
        if not f.endswith(".py"):
            continue
        src = open(os.path.join(PLATFORMS, f), encoding="utf-8").read()
        for m in re.finditer(r"from\s+\.\s+import\s+([A-Za-z_][\w,\s]*)", src):
            for name in [x.strip() for x in m.group(1).split(",")]:
                if name in upper:
                    bad.append(f"{f}: from . import {name}  → 应为 from .. import {name}")
    check("没有单个点导入上层模块的写错", not bad, str(bad))

    print("【3】每个平台模块都能导入(编译级 smoke)")
    sys.path.insert(0, KERNEL)
    import importlib
    for mod in sorted(here):
        try:
            importlib.import_module(f"merchant_report.platforms.{mod}")
            ok, err = True, ""
        except Exception as e:
            ok, err = False, repr(e)[:90]
        check(f"import merchant_report.platforms.{mod}", ok, err)

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 条失败: {FAILS}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
