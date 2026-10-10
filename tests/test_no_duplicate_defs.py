# -*- coding: utf-8 -*-
"""防呆: 同一个类里不允许重复定义同名方法。

Python 只有**最后一个**定义生效 → 「明明改对了, 跑的却还是旧代码」这类坑就是这么来的:
淘宝 TaobaoAdapter._calendar_frame 曾重复定义 4 份、_read_selected_range 3 份,
改版修复只改了其中一份就以为修好了。

KNOWN 里的历史重复**只许减少不许增加**; 出现新的重复定义直接失败。
"""
import ast
import pathlib

PKG = pathlib.Path(__file__).resolve().parents[1] / "kernel" / "merchant_report"

# 已知历史重复(待清理): 只允许 <= 这里的数量
KNOWN = {
    "taobao.py": {
        "TaobaoAdapter._calendar_frame": 4,
        "TaobaoAdapter._calendar_open": 4,
        "TaobaoAdapter._read_selected_range": 3,
        "TaobaoAdapter._click_calendar_date": 3,
        "TaobaoAdapter._pick_custom_range": 3,
    },
}


def scan():
    found = {}
    for py in sorted(PKG.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            seen = {}
            for fn in node.body:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    seen.setdefault(fn.name, []).append(fn.lineno)
            for name, lines in seen.items():
                if len(lines) > 1:
                    found[f"{py.name}|{node.name}.{name}"] = (len(lines), lines)
    return found


def main():
    found = scan()
    problems = []
    allowed_total = allowed_hit = 0
    for key, (n, lines) in sorted(found.items()):
        fname, method = key.split("|")
        cap = KNOWN.get(fname, {}).get(method, 1)
        allowed_total += 1
        if n > cap:
            problems.append(f"❌ {fname} 的 {method} 重复定义 {n} 次(允许 {cap}) 行 {lines}")
        else:
            allowed_hit += 1
            print(f"⚠️  {fname} {method} 旧重复 {n} 份(允许 ≤{cap}) 行 {lines} —— 待清理")

    # 清单里的条目如果已被清理, 提醒收缩清单(不失败, 只是提示)
    for fname, methods in KNOWN.items():
        for method in methods:
            if f"{fname}|{method}" not in found:
                print(f"🎉 {fname} {method} 已清理干净 —— 请把 KNOWN 里的这条删掉")

    print(f"\n扫描完成: {len(found)} 处重复定义, {allowed_hit}/{allowed_total} 在允许清单内")
    if problems:
        for p in problems:
            print(p)
        raise SystemExit("❌ 出现新的重复定义(改代码前先 grep 'def 方法名' 数一下)")
    print("✅ 无新增重复定义(旧重复只许减少)")


if __name__ == "__main__":
    main()
