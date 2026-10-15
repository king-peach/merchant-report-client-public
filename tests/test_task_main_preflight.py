# -*- coding: utf-8 -*-
"""M1 子进程集成: 开工前检查失败必须**可见**(进 stdout 事件流 + 落盘 ndjson), 而不是一行裸 JSON。

2026-09-14 真机: 4 平台 + 管家/京东整天的任务全部倒在开工检查上, 而 EventSink 当时在检查
**之后**才创建 → 失败只 print 了一行 JSON, 不落盘、不进汇总, 用户日志只剩一行 date=(盲区)。
本用例走"模板不存在"分支(零等待 —— 绝不触发 60 秒自动等待)。
"""
import glob
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "kernel"

print("=== 开工检查失败(模板不存在) → log 事件 + result 事件 + ndjson 落盘 ===")
with tempfile.TemporaryDirectory() as tmp:
    missing = os.path.join(tmp, "不存在的模板.xlsx")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(KERNEL)
    p = subprocess.run([sys.executable, "-m", "merchant_report.task_main",
                        "--platform", "taobao",
                        "--template", missing,
                        "--download-dir", tmp,
                        "--merchant", "测试",
                        "--date", "2026-09-13"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    out = p.stdout or ""
    print(f"  exit={p.returncode} stdout 行数={len([l for l in out.splitlines() if l.strip()])}")
    assert p.returncode == 1, (p.returncode, out[-500:], (p.stderr or "")[-500:])

    lines = [l for l in out.splitlines() if l.strip()]
    objs = [json.loads(l) for l in lines]          # stdout 必须仍是**纯单行 JSON** 协议
    logs = [o for o in objs if o.get("type") == "log"]
    results = [o for o in objs if o.get("type") == "result"]
    assert logs and any("开工检查" in (o.get("msg") or "") for o in logs), logs
    assert results and results[-1].get("error_code") == "E_TEMPLATE_MISSING", results
    assert results[-1].get("ok") is False and results[-1].get("retryable") is False, results[-1]
    assert "模板文件不存在" in (results[-1].get("message") or ""), results[-1]
    print(f"  log: {logs[0]['msg'][:46]}… ✓")
    print(f"  result: {results[-1]['error_code']} / retryable={results[-1]['retryable']} ✓")

    nd = glob.glob(os.path.join(tmp, "logs", "run_*.ndjson"))
    assert len(nd) == 1, (nd, os.listdir(tmp))
    body = open(nd[0], encoding="utf-8").read()
    nd_objs = [json.loads(l) for l in body.splitlines() if l.strip()]
    assert any(o.get("type") == "log" and "开工检查" in (o.get("msg") or "") for o in nd_objs), body
    assert any(o.get("type") == "result" and o.get("error_code") == "E_TEMPLATE_MISSING"
               for o in nd_objs), body
    print(f"  落盘 {os.path.basename(nd[0])}: {len(nd_objs)} 条事件, 含 log+result ✓")

print("\n✅ M1 开工失败可见化(子进程集成)通过")
