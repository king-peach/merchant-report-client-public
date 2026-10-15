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
    ap.add_argument("--store-details", default=None, help="门店明细表路径(设置里配置; 编号优先匹配, 未配置=旧行为)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--show-browser", action="store_true",
                    help="「展示过程」开关开启时启动: 浏览器不藏屏外, 开场直接滑入主屏")
    args = ap.parse_args()

    sys.stderr.write(f"[kernel] merchant={args.merchant} platform={args.platform} "
                     f"scope={args.scope} store_id={args.store_id} date={args.date}\n")

    # EventSink 必须**早于开工前检查**创建: 2026-09-14 真机 —— 4 平台 + 管家/京东整天全部倒在
    # 这几项检查上, 而失败只打了一行裸 JSON, 既不落盘日志也不进汇总 → 用户日志只剩一行 date=,
    # 完全看不出"当天为什么什么都没跑"。检查失败同样是**任务结果**, 必须走 sink(落盘 + 界面可见)。
    # sink 只依赖 args.download_dir(不依赖 cfg), 所以能安全提前到这里。
    sink = EventSink("task-" + datetime.now().strftime("%Y%m%d-%H%M%S"), to_stdout=True,
                     echo_ndjson_path=_run_log_path(args.download_dir, args.platform))
    if sink.echo_path:
        sys.stderr.write(f"[kernel] 本次运行日志(可复查/可发我): {sink.echo_path}\n")

    def _preflight_fail(code, message, retryable):
        """开工检查未通过: 先给人看的日志、再发结果事件、落盘后退出(保证 ndjson flush)。"""
        sink.log(f"❌ 开工检查未通过: {message}", level="warn")
        sink.result(ok=False, error_code=code, message=message, retryable=retryable)
        sink.close()
        sys.exit(1)

    # 模板兜底校验(壳已查一次; 双保险)
    if not os.path.isfile(args.template):
        _preflight_fail("E_TEMPLATE_MISSING",
                        f"模板文件不存在: {args.template} (可能被移动或改名，请到设置重新选择)", False)
    # 可写性预检(2026-10-01 Windows 真机: 模板开在 Excel 里 → 采集跑完几分钟才在保存时炸
    #   `[Errno 13] Permission denied`; 现在**开工前**就报清楚, 别浪费用户的时间)
    # 2026-10-15 升级: 不再"一看被占就失败" —— 用户当场关掉文件就能接着跑, 所以**自动等待释放**
    # (最多 60 秒, 每 5 秒一探, 释放即继续), 期间把"谁占着/还等多久"实时打给用户。
    try:
        from .fill import ensure_writable, wait_writable
        _ok, _why = ensure_writable(args.template)
        if not _ok:
            if not wait_writable(args.template, "模板", total_s=60, poll_s=5,
                                 log=lambda m: sink.log(m, level="warn")):
                _preflight_fail("E_TEMPLATE_LOCKED", f"{_why}（已自动等待 60 秒仍未释放）", True)
    except SystemExit:
        raise
    except Exception:
        # 宽容性保留: 探测环节自身出问题(非 Windows 权限/异常环境)不许拦任务 —— 放行给后续逻辑,
        # 真有问题会在保存时以明确文案暴露(不因"检查器坏了"误杀正常任务)。
        pass
    if args.scope == "store" and not args.store_id:
        _preflight_fail("E_NO_STORE", "单店模式需选择门店(先跑一次全部门店任务以发现门店)", False)

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
        "store_details": args.store_details or "",
        "credentials": {"username": args.username, "password": args.password},
    }
    human = HumanBridge(from_stdin=True)
    # 常驻 stdin 分发器(需求 F): 一个读取线程按类型分发 —— 人工响应 → HumanBridge 队列;
    # 「展示过程」开关 → browser_window.request(只改标志, CDP 仍由主线程安全点执行)。
    # 必须**早于**任何需要读 stdin 的环节启动(幂等, 重复调用无副作用)。
    from merchant_report import stdin_router
    stdin_router.start()
    llm = LLMClient(cfg.get("llm", {}).get("base_url"), cfg.get("llm", {}).get("api_key"),
                    cfg.get("llm", {}).get("model"))
    try:
        run_task(cfg, store, date=args.date, date_from=date_from, sink=sink, human=human,
                 skip_browser=args.no_browser, llm_client=llm,
                 show_browser=args.show_browser)
    except Exception as e:
        sink.result(ok=False, error_code="E_CRASH", message=str(e)[:300], retryable=True)
        sys.stderr.write(f"[kernel] crash: {e}\n")
        sys.exit(1)
    finally:
        sink.close()


if __name__ == "__main__":
    main()
