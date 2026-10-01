# -*- coding: utf-8 -*-
"""壳模式入口: py_runner.rs spawn 本模块, NDJSON 走 stdout, 人工响应走 stdin。
商家/平台/模板/范围由壳传入(前端配置); 模板存在性二次校验兜底。"""
import argparse
import os
import sys
from datetime import datetime, timedelta

from merchant_report.config import load_config
from merchant_report.events import EventSink, HumanBridge
from merchant_report.llm import LLMClient
from merchant_report.orchestrator import run_task


def _ensure_utf8_streams():
    """Windows 上 Python 的 stdout 默认用 ANSI 代码页(cp936/ascii), 而事件流里满是中文,
    写入会抛 UnicodeEncodeError; 无控制台时 stdout 甚至可能是 None。
    统一重配为 UTF-8 + errors=replace, 保证事件流不因编码中断(壳按 UTF-8 解码)。
    必须在内核 main() 最开始调用: 早于任何事件发射。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            if stream is None:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            elif hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _run_log_path(download_dir, platform) -> str:
    """每次运行的 NDJSON 事件日志落盘路径(用户模板旁边 logs/ 目录)。

    为什么要落盘: 壳只把事件流打到界面上, 关掉窗口/log 丢了 → 用户说"日志显示完成了但表里没数据"
    时无从复查(2026-09-30 真机就是这么卡住的: 只能靠翻模板文件倒推)。
    """
    base = download_dir or os.path.join(os.path.expanduser("~"), ".hermes",
                                        "merchant-report-cache")
    d = os.path.join(base, "logs")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return ""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(d, f"run_{ts}_{platform or 'task'}.ndjson")


def main():
    _ensure_utf8_streams()
    ap = argparse.ArgumentParser()
    ap.add_argument("--merchant", required=True, help="商家名")
    ap.add_argument("--platform", default="taobao", choices=["taobao", "meituan", "meituan_gj", "jd"])
    ap.add_argument("--template", required=True, help="目标模板表路径")
    ap.add_argument("--keyword", default="", help="平台门店匹配关键词(如 银盆岭)")
    ap.add_argument("--scope", default="all", choices=["all", "store"])
    ap.add_argument("--store-id", default="", help="单店模式的平台门店ID")
    ap.add_argument("--date", default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
    ap.add_argument("--date-from", default=None, help="报表区间起始(默认=--date)")
    ap.add_argument("--username", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--download-dir", default=None, help="报表下载/查找目录(壳传入)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    sys.stderr.write(f"[kernel] merchant={args.merchant} platform={args.platform} "
                     f"scope={args.scope} store_id={args.store_id} date={args.date}\n")

    # 模板兜底校验(壳已查一次; 双保险)
    if not os.path.isfile(args.template):
        print('{"type":"result","ok":false,"error_code":"E_TEMPLATE_MISSING",'
              '"message":"模板文件不存在: %s (可能被移动或改名，请到设置重新选择)","retryable":false}' % args.template)
        sys.exit(1)
    # 可写性预检(2026-10-01 Windows 真机: 模板开在 Excel 里 → 采集跑完几分钟才在保存时炸
    #   `[Errno 13] Permission denied`; 现在**开工前**就报清楚, 别浪费用户的时间)
    try:
        import json as _json
        from .fill import ensure_writable
        _ok, _why = ensure_writable(args.template)
        if not _ok:
            print(_json.dumps({"type": "result", "ok": False, "error_code": "E_TEMPLATE_LOCKED",
                               "message": _why, "retryable": True}, ensure_ascii=False))
            sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        pass
    if args.scope == "store" and not args.store_id:
        print('{"type":"result","ok":false,"error_code":"E_NO_STORE",'
              '"message":"单店模式需选择门店(先跑一次全部门店任务以发现门店)","retryable":false}')
        sys.exit(1)

    cfg = load_config(args.config)
    if args.download_dir:
        cfg.setdefault("defaults", {})["download_dir"] = args.download_dir
    date_from = args.date_from or args.date
    store = {
        "name": args.merchant,
        "id": args.store_id,
        "keyword": args.keyword,
        "template": args.template,
        "platform": args.platform,
        "scope": args.scope,
        "credentials": {"username": args.username, "password": args.password},
    }
    sink = EventSink("task-" + datetime.now().strftime("%Y%m%d-%H%M%S"), to_stdout=True,
                     echo_ndjson_path=_run_log_path(args.download_dir, args.platform))
    if sink.echo_path:
        sys.stderr.write(f"[kernel] 本次运行日志(可复查/可发我): {sink.echo_path}\n")
    human = HumanBridge(from_stdin=True)
    llm = LLMClient(cfg.get("llm", {}).get("base_url"), cfg.get("llm", {}).get("api_key"),
                    cfg.get("llm", {}).get("model"))
    try:
        run_task(cfg, store, date=args.date, date_from=date_from, sink=sink, human=human,
                 skip_browser=args.no_browser, llm_client=llm)
    except Exception as e:
        sink.result(ok=False, error_code="E_CRASH", message=str(e)[:300], retryable=True)
        sys.stderr.write(f"[kernel] crash: {e}\n")
        sys.exit(1)
    finally:
        sink.close()


if __name__ == "__main__":
    main()
