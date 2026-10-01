# -*- coding: utf-8 -*-
"""CLI 入口(M1 验证用; 壳模式由 src-tauri py_runner 直接调 run_task)。"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .config import load_config
from .events import EventSink, HumanBridge
from .llm import LLMClient
from .orchestrator import run_task


def _cli_human(obj):
    """CLI 人工操作: 终端提示"""
    print(f"\n⌨️  [需人工] {obj.get('message')}", file=sys.stderr)
    if obj.get("action") == "confirm":
        try:
            c = input(f"   选项 {'/'.join(obj.get('choices', ['继续']))}: ")
            return {"type": "human_response", "action": "confirm", "choice": c}
        except EOFError:
            return {"type": "human_response", "action": "confirm", "choice": "终止"}
    try:
        input("   完成后回车继续...")
    except EOFError:
        pass
    return {"type": "human_response", "action": obj.get("action"), "result": "done"}


def _render(obj):
    """NDJSON → 人类可读(CLI 模式)"""
    t = obj.get("type")
    if t == "task_start":
        print(f"\n▶ 任务 {obj['task_id']}: {obj['store']} @ {obj['date']}")
    elif t == "step":
        icon = {"running": "⏳", "ok": "✅", "failed": "❌", "skipped": "⏭️"}.get(obj["state"], "·")
        line = f"  {icon} {obj['name']}: {obj['state']}"
        if obj.get("detail"):
            line += f" — {obj['detail']}"
        if obj.get("elapsed_s"):
            line += f" ({obj['elapsed_s']}s)"
        print(line, file=sys.stderr if obj["state"] == "failed" else sys.stdout)
    elif t == "log":
        print(f"  💬 {obj['msg']}")
    elif t == "need_human":
        pass  # 由 HumanBridge 渲染
    elif t == "llm_delta":
        print(obj["text"], end="", flush=True)
    elif t == "llm_done":
        print()
    elif t == "result":
        s = "✅ 完成" if obj["ok"] else f"❌ 失败[{obj.get('error_code')}]: {obj.get('message')}"
        print(f"\n{s} (耗时 {obj.get('elapsed_s')}s)")
        if obj.get("summary"):
            print(json.dumps(obj["summary"], ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(prog="merchant_report")
    sub = ap.add_subparsers(dest="cmd", required=True)
    runp = sub.add_parser("run", help="执行单店日报任务")
    runp.add_argument("--store", required=True, help="门店名关键字")
    runp.add_argument("--date", default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
    runp.add_argument("--config", default=None)
    runp.add_argument("--mode", choices=["top", "auto"], default=None)
    runp.add_argument("--no-browser", action="store_true", help="跳过浏览器(用目录已有报表)")
    runp.add_argument("--no-llm", action="store_true")
    runp.add_argument("--raw", action="store_true", help="输出原始 NDJSON(模拟壳)")
    testp = sub.add_parser("test-llm", help="测试 LLM 连接")
    testp.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.mode:
        cfg.setdefault("defaults", {})["fill_mode"] = args.mode

    if args.cmd == "test-llm":
        llm = LLMClient(cfg.get("llm", {}).get("base_url"), cfg.get("llm", {}).get("api_key"),
                        cfg.get("llm", {}).get("model"))
        ok, msg = llm.test_connection()
        print(("✅ " if ok else "❌ ") + msg)
        sys.exit(0 if ok else 1)

    stores = cfg.get("stores") or []
    hits = [s for s in stores if args.store in s["name"] or args.store == str(s.get("id"))]
    if not hits:
        print("!! 未找到门店:", args.store, "| 可选:", [s["name"] for s in stores])
        sys.exit(2)
    store = hits[0]

    sink = EventSink("cli-" + datetime.now().strftime("%H%M%S"), to_stdout=args.raw,
                     renderer=None if args.raw else _render)
    human = HumanBridge(cli_callback=_cli_human)
    llm = None if args.no_llm else LLMClient(cfg.get("llm", {}).get("base_url"),
                                             cfg.get("llm", {}).get("api_key"),
                                             cfg.get("llm", {}).get("model"))
    try:
        if not args.raw:
            # CLI 双写: 渲染 + 原始留档
            import io
            class _Tee(EventSink):
                pass
        r = run_task(cfg, store, date=args.date, sink=sink, human=human,
                     skip_browser=args.no_browser, llm_client=llm)
        sys.exit(0 if r else 1)
    finally:
        sink.close()


if __name__ == "__main__":
    main()
