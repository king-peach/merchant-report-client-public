# -*- coding: utf-8 -*-
"""构建身份: 让"我现在跑的到底是哪个包"一眼可见。

打包时由 scripts/stamp_build.sh 写入 build_info.json(提交号/时间/渠道);
仓库直跑(开发)时没有该文件 → 显示"开发版"。目的: 避免"装了两三个包、分不清在跑哪个"
导致把旧版行为当成新 bug 排查(实战踩过: 用户机器上挂载了 3 个 DMG 卷)。
"""
import json
import os


def _paths():
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    return (os.path.join(here, "build_info.json"),
            os.path.join(parent, "build_info.json"))


def info():
    for p in _paths():
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("commit"):
                return d
        except Exception:
            continue
    return {}


def short():
    d = info()
    return str(d.get("commit") or "")


def version_line():
    d = info()
    if not d:
        return "🧩 内核版本: 开发版(仓库直跑, 无构建戳)"
    bits = [f"🧩 内核版本: {d.get('commit', '?')}"]
    if d.get("built_at"):
        bits.append(f"构建于 {d['built_at']}")
    if d.get("channel"):
        bits.append(f"渠道 {d['channel']}")
    if d.get("note"):
        bits.append(str(d["note"]))
    return " | ".join(bits)
