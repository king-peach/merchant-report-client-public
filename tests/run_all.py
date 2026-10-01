# -*- coding: utf-8 -*-
"""跑 tests/ 下所有单测并汇总(每个测试独立进程, 避免相互污染)。

用法: python3 tests/run_all.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = sorted(f for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))


def main():
    bad = []
    for f in FILES:
        p = subprocess.run([sys.executable, os.path.join(HERE, f)],
                           capture_output=True, text=True, timeout=900)
        last = [ln for ln in (p.stdout or "").strip().splitlines() if ln.strip()]
        tail = last[-1] if last else "(无输出)"
        mark = "✅" if p.returncode == 0 else "❌"
        print(f"{mark} {f:36s} {tail[:70]}", flush=True)
        if p.returncode != 0:
            bad.append(f)
            print("   " + "\n   ".join((p.stdout or "").strip().splitlines()[-6:]), flush=True)
            if (p.stderr or "").strip():
                print("   stderr: " + (p.stderr or "").strip()[-300:], flush=True)
    print(f"\n{'✅ 全部通过' if not bad else '❌ 失败: ' + ', '.join(bad)}  ({len(FILES)} 个测试文件)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
