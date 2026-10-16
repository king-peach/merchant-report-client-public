# -*- coding: utf-8 -*-
"""淘宝切店「按名回退」probe 提取 单测(2026-10-16)。

背景: `_switch_by_name`(老回退流程)的 probe 曾把品牌字面量硬编码进可执行语句
(`name.strip("<品牌>…()")`) —— 脱敏同步(公开仓)必然改写这条**可执行语句** →
两个构建来源行为分叉。现改为品牌无关的「括注内定位词」提取; 本测试锁该函数行为 + 接线。

不连浏览器; 用例全用中性店名(不含真实品牌), 公开脱敏树同样可跑。
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import taobao as TB                              # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    cases = [
        ("甲品牌·系列一(长沙高桥店)", "长沙高桥店"),      # 半角括号
        ("甲品牌·系列一（梅溪湖店）", "梅溪湖店"),        # 全角括号
        ("甲品牌旗舰店", ""),                           # 无括注 → 空(该 probe 跳过)
        ("", ""),                                       # 空名
        ("甲品牌(乙店)(丙店)", "乙店"),                  # 多个括注取第一个
    ]
    for src, want in cases:
        got = TB._locality_probe(src)
        check(f"_locality_probe({src!r}) == {want!r}", got == want, f"got {got!r}")

    src_tb = inspect.getsource(TB)
    check("接线: taobao.py 使用 _locality_probe(name)", "_locality_probe(name)" in src_tb)

    print(f"\n{'✅ 全部通过' if not FAILS else '❌ 失败: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
