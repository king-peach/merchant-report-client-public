# -*- coding: utf-8 -*-
"""内核 stdin 常驻分发器 单测 (2026-10-14, 需求 F「展示过程」)。

背景: stdin 原先**只在** HumanBridge.wait() 里临时开 daemon 线程 readline —— 一次只允许
一个读者, 而且只在"等人工响应"时才读。需求 F 之后 stdin 上跑多路消息:
  · {"type":"human_response", ...}                    人工响应(面板点了 / 超时回执)
  · {"type":"browser_window","visible":true|false}     「展示过程」开关
临时读者会**抢行/丢消息**(两条线程同时 readline, 一条会把另一条的行吃掉)。
本测试钉死: ①单读者(常驻分发器), 重复 start 幂等; ②人工响应入队 → HumanBridge.wait 取到;
③browser_window 行 → browser_window.request 被正确调用(且**只改标志**);
④垃圾行/未知类型忽略不炸; ⑤超时语义不变(无人应答 → 按 limit 返回 None)。
"""
import inspect
import json
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report import browser_window as BW          # noqa: E402
from merchant_report import stdin_router as SR            # noqa: E402
from merchant_report.events import HumanBridge            # noqa: E402


class _Pipe:
    """可控 stdin 替身: 喂一行吐一行, 喂完就**阻塞**(真实管道语义 —— 读取线程常驻等着)。"""

    def __init__(self, lines=()):
        self._q = []
        for ln in lines:
            self._q.append(ln)
        self._lock = threading.Lock()

    def push(self, line):
        with self._lock:
            self._q.append(line)

    def readline(self):
        while True:
            with self._lock:
                if self._q:
                    return self._q.pop(0)
            time.sleep(0.02)

    def close(self):
        """EOF: 读取线程自己收工。"""
        with self._lock:
            self._q.append("")


def _router(pipe):
    """新建一个读指定管道的分发器并装成全局单例(不碰进程全局状态以外的副作用:
    上一条用例的读取线程还阻塞在它自己的管道替身上, daemon 线程不影响后续)。
    HumanBridge.wait 取的就是**当前全局**分发器 —— 这正是壳模式下的真实路径。"""
    SR._singleton = SR.StdinRouter(stream=pipe)
    return SR._singleton.start()


def test_human_response_reaches_bridge():
    pipe = _Pipe()
    _router(pipe)
    bridge = HumanBridge(from_stdin=True)
    t = threading.Thread(target=lambda: None)
    got = {}

    def waiter():
        got["r"] = bridge.wait({"type": "need_human", "action": "confirm"}, timeout_s=5)

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    time.sleep(0.15)
    pipe.push(json.dumps({"type": "human_response", "choice": "继续"}, ensure_ascii=False) + "\n")
    t.join(timeout=5)
    assert got.get("r") == {"type": "human_response", "choice": "继续"}, got
    print("✅ human_response: stdin 一行 → HumanBridge.wait 取到(常驻分发器单读者)")


def test_browser_window_toggles_request():
    """browser_window 行 → browser_window.request(visible) 被调用; 且**只改标志**(不碰 CDP)。"""
    calls = []
    real = BW.request
    BW.request = lambda visible, sink=None: calls.append(bool(visible))     # noqa: E731
    try:
        pipe = _Pipe()
        r = _router(pipe)
        pipe.push(json.dumps({"type": "browser_window", "visible": True}) + "\n")
        pipe.push(json.dumps({"type": "browser_window", "visible": False}) + "\n")
        deadline = time.time() + 5
        while len(calls) < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert calls == [True, False], f"开关消息没有正确转给 browser_window.request: {calls}"
        assert r.feed('{"type":"browser_window"}') == "browser_window", \
            "缺 visible 字段要按 False 处理(收回), 不许抛"
    finally:
        BW.request = real
    print("✅ browser_window: 开关消息 → request(visible); 读取线程不碰 CDP")


def test_request_only_sets_flags():
    """铁律: request() 只能改标志 —— stdin 读取线程绝不许碰 CDP(Playwright sync 绑主线程)。"""
    src = inspect.getsource(BW.request)
    assert "send(" not in src and "new_browser_cdp_session" not in src, \
        "request() 里出现了 CDP 调用 —— 这条路径会被 stdin 读取线程执行! 会直接搞崩任务"
    assert "_pending" in src and "_manual" in src
    print("✅ 线程纪律: request() 只改标志, 零 CDP")


def test_garbage_and_unknown_ignored():
    pipe = _Pipe()
    r = _router(pipe)
    assert r.feed("这不是 JSON\n") == "garbage"
    assert r.feed("[1,2,3]") == "garbage"          # JSON 但不是对象
    assert r.feed('{"type":"完全不认识的类型"}') == "unknown"
    assert r.feed("") == "garbage"
    assert r.human_queue.empty(), "垃圾行绝不能污染人工响应队列"
    # 未知类型只提示一次(别刷屏)
    assert len(r._noted) == 1
    print("✅ 垃圾行/未知类型: 忽略不炸, 也不污染人工队列")


def test_timeout_semantics_kept():
    """无人应答 → wait 按 limit 返回 None(与 2026-10-13 的超时兜底语义完全一致)。"""
    pipe = _Pipe()          # 永远不会 push
    _router(pipe)
    t0 = time.time()
    r = HumanBridge(from_stdin=True).wait({"type": "need_human"}, timeout_s=0.4)
    dt = time.time() - t0
    assert r is None, r
    assert dt < 5, f"超时未生效(等了 {dt:.1f}s)"
    print(f"✅ 超时语义: 无人应答 → {dt:.1f}s 返回 None")


def test_timeout_receipt_is_delivered():
    """壳侧超时回执 {timeout:true} 也要送到 HumanBridge(调用方按无人值守兜底)。"""
    pipe = _Pipe([json.dumps({"type": "human_response", "timeout": True}) + "\n"])
    _router(pipe)
    r = HumanBridge(from_stdin=True).wait({"type": "need_human"}, timeout_s=5)
    assert r and r.get("timeout") is True, r
    print("✅ 超时回执: 壳发的 {timeout:true} 照常送达")


def test_start_idempotent():
    """整个进程只有一个读取线程: 重复 start() 不许再开第二条(单读者红线)。"""
    pipe = _Pipe()
    r = SR.StdinRouter(stream=pipe)
    a = r.start()
    t1 = r.reader_thread
    b = r.start()
    assert a is b is r
    assert r.reader_thread is t1, "重复 start 又开了一条读取线程 —— 会跟已有读者抢行!"
    assert t1.daemon is True, "读取线程必须 daemon(不阻塞进程退出)"
    assert t1.is_alive()
    r.feed('{"type":"human_response","choice":"x"}')
    assert r.human_queue.get(timeout=1) == {"type": "human_response", "choice": "x"}
    print("✅ 幂等: 重复 start 不开第二条读取线程")


def test_stream_resolved_lazily():
    """读取线程每次取行时重新解析 sys.stdin —— 测试/壳替换 stdin 能生效。"""
    a, b = _Pipe([json.dumps({"type": "human_response", "choice": "A"}) + "\n"]), \
        _Pipe([json.dumps({"type": "human_response", "choice": "B"}) + "\n"])
    old = sys.stdin
    sys.stdin = b
    try:
        r = SR.StdinRouter(stream=a)     # 显式流优先
        assert r.feed(a.readline()) == "human"
    finally:
        sys.stdin = old
    print("✅ 流解析: 显式注入的流优先(测试可控), 否则用当前 sys.stdin")


def test_task_main_wiring():
    """壳模式入口要启动分发器(幂等), 否则「展示过程」开关到不了内核。"""
    from merchant_report import task_main
    src = inspect.getsource(task_main.main)
    assert "stdin_router.start()" in src, "task_main 必须启动常驻 stdin 分发器"
    assert "--show-browser" in src and "show_browser=args.show_browser" in src, \
        "开场滑入参数没接上"
    print("✅ 接线: task_main 启动分发器 + 透传 --show-browser")


def main():
    test_human_response_reaches_bridge()
    test_browser_window_toggles_request()
    test_request_only_sets_flags()
    test_garbage_and_unknown_ignored()
    test_timeout_semantics_kept()
    test_timeout_receipt_is_delivered()
    test_start_idempotent()
    test_stream_resolved_lazily()
    test_task_main_wiring()
    print("\n✅ stdin 常驻分发器 全绿")


if __name__ == "__main__":
    main()
