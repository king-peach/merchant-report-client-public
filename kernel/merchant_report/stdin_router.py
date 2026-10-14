# -*- coding: utf-8 -*-
"""内核 stdin 常驻分发器(2026-10-14, 需求 F)。

背景: stdin 原先**只在** HumanBridge.wait() 里临时开 daemon 线程 readline —— 一次只能有一个
读者, 而且只在"等人工响应"时才读。现在 stdin 上跑多路消息:
  · {"type":"human_response", ...}                     人工响应(面板点了 / 超时回执)
  · {"type":"browser_window", "visible": true|false}    「展示过程」开关(运行中随时可切)
临时读者会**抢行/丢消息**(两条线程同时 readline, 一条会把另一条的行吃掉, 拿到的还不是自己的)。
→ 整个进程只留**一个**常驻读取线程, 按 type 分发(单读者红线)。

铁律: 读取线程**只许改标志 / 入队**, 绝不碰 CDP / Playwright —— 那是主线程的活
      (Playwright sync API 绑主线程, 跨线程发 CDP 会直接把任务搞崩)。
"""
import json
import queue
import sys
import threading

_HUMAN = "human_response"
_WINDOW = "browser_window"


class StdinRouter:
    """常驻 stdin 读取 + 按类型分发。start() 幂等(重复调用不会开第二条读取线程)。"""

    def __init__(self, stream=None, name="mrc-stdin-router"):
        self._stream = stream              # None = 用当前 sys.stdin(测试可注入替身)
        self._name = name
        self._q = queue.Queue()            # human_response → HumanBridge 消费
        self._lock = threading.Lock()
        self._started = False
        self._thread = None
        self._noted = set()                # 未知类型只提示一次(别刷屏)

    # ---- 生命周期 ----
    @property
    def started(self):
        return self._started

    def start(self):
        """幂等: 已有活着的读取线程 → 直接返回; 线程已退出(EOF/异常) → 重新武装一条。"""
        with self._lock:
            t = self._thread
            if t is not None and t.is_alive():
                return self
            self._started = True
            t = threading.Thread(target=self._loop, name=self._name, daemon=True)
            self._thread = t
        t.start()
        return self

    @property
    def reader_thread(self):
        return self._thread

    @property
    def human_queue(self):
        return self._q

    def _stream_now(self):
        """每次取行时**重新解析** sys.stdin(而不是启动时固定死) —— 测试替换 sys.stdin 能生效。"""
        return self._stream if self._stream is not None else sys.stdin

    def _loop(self):
        while True:
            try:
                line = self._stream_now().readline()
            except Exception:
                return                      # 通道异常 → 收工(调用方按超时兜底)
            if not line:
                return                      # EOF: 壳关了 stdin → 不忙等, 直接收工
            self.feed(line)

    # ---- 分发 ----
    def feed(self, line):
        """处理一行。返回 "human"/"browser_window"/"garbage"/"unknown" (便于单测断言)。"""
        try:
            obj = json.loads(line)
        except Exception:
            return "garbage"
        if not isinstance(obj, dict):
            return "garbage"
        t = obj.get("type")
        if t == _HUMAN or t is None:
            # 超时回执 {type:"human_response", timeout:true} 也走这条: 语义 = "没人点",
            # 调用方按无人值守兜底(与"通道不可用"同路)。
            # t is None(裸响应体, 无 type)按人工响应处理 —— 兼容旧壳/旧测试的裸 JSON 回执。
            self._q.put(obj)
            return "human"
        if t == _WINDOW:
            # 「展示过程」开关: **只改标志**(request 内部绝不碰 CDP), 主线程安全点执行。
            try:
                from . import browser_window as bw
                bw.request(bool(obj.get("visible")))
            except Exception:
                pass
            return _WINDOW
        self._note(t)
        return "unknown"

    def _note(self, t):
        key = str(t)
        if key in self._noted:
            return
        self._noted.add(key)
        try:
            sys.stderr.write(f"[stdin_router] 忽略未知消息类型: {key}\n")
            sys.stderr.flush()
        except Exception:
            pass


_singleton = None
_singleton_lock = threading.Lock()


def start(stream=None):
    """启动(或复用)全局分发器 —— 幂等。壳模式入口(task_main)调用一次即可。"""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = StdinRouter(stream=stream)
        r = _singleton
    return r.start()


def current():
    """当前全局分发器(未启动 → None)。HumanBridge 用它取队列。"""
    return _singleton
