# -*- coding: utf-8 -*-
"""NDJSON 事件流（协议 v1，见 docs/任务协议-NDJSON.md）
内核 → 壳: stdout 逐行 JSON; 壳 → 内核: stdin 逐行 JSON。
CLI 模式下人类可读渲染。"""
import json
import sys
import time


class EventSink:
    """事件发射器。to_stdout=True 时写真实 stdout（壳模式）；
    CLI 模式传 renderer 回调渲染人类可读输出，原始 NDJSON 另存。"""

    def __init__(self, task_id, to_stdout=True, echo_ndjson_path=None, renderer=None):
        self.task_id = task_id
        self.to_stdout = to_stdout
        self.renderer = renderer
        self.echo_path = echo_ndjson_path
        self._echo_fp = None
        self._stdout_broken = False
        self._fallback_path = None
        if echo_ndjson_path:
            try:
                self._echo_fp = open(echo_ndjson_path, "a", encoding="utf-8")
            except Exception:
                self._echo_fp = None
        self.t0 = time.time()
        # 登记事件出口(需求 F): 各平台**等待循环**里的 browser_window.poll() 不带 sink 时,
        # 用它把「已弹回主屏 / 已收回屏外」反馈给用户(没有它那些日志会丢)。
        # 只认**壳模式**的 sink(to_stdout): CLI 的 renderer 出口不该被窗口控制的日志污染。
        if to_stdout:
            try:
                from . import browser_window as _bw
                _bw.set_sink(self)
            except Exception:
                pass

    def _fallback_write(self, line, err):
        """stdout 写不进去时的兜底(Windows: 管道读端已关/编码异常 → OSError EINVAL)。
        关键: 绝不能让输出故障掩盖真实错误。降级 = ①stderr(壳会展示为"内核诊断输出")
        ②落盘一份事件日志。只提示一次, 避免刷屏。"""
        if not self._stdout_broken:
            self._stdout_broken = True
            try:
                sys.stderr.write(f"[events] stdout 写入失败({err.__class__.__name__}: {err}), "
                                 f"已降级为 stderr + 落盘\n")
                sys.stderr.flush()
            except Exception:
                pass
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except Exception:
            pass
        try:
            if self._fallback_path is None:
                from .config import app_data_dir
                self._fallback_path = app_data_dir() / "events_fallback.ndjson"
            with open(self._fallback_path, "a", encoding="utf-8") as fp:
                fp.write(line + "\n")
        except Exception:
            pass

    def _emit(self, obj):
        obj.setdefault("task_id", self.task_id)
        line = json.dumps(obj, ensure_ascii=False)
        if self._echo_fp:
            try:
                self._echo_fp.write(line + "\n")
                self._echo_fp.flush()
            except Exception:
                pass
        if self.to_stdout and not self._stdout_broken:
            try:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            except Exception as e:
                self._fallback_write(line, e)
        elif self.to_stdout and self._stdout_broken:
            self._fallback_write(line, OSError("stdout 已标记为不可用"))
        if self.renderer:
            try:
                self.renderer(obj)
            except Exception:
                pass
        return obj

    # ---- 生命周期 ----
    def task_start(self, store, date, platforms):
        return self._emit({"type": "task_start", "store": store, "date": date, "platforms": platforms})

    def result(self, ok, outputs=None, summary=None, error_code=None, message=None, retryable=False):
        # 后台模式兜底: 任务收尾无论走到哪条路, 都把窗口收回屏幕外(幂等)
        try:
            from . import browser_window as _bw
            _bw.hide()
        except Exception:
            pass
        return self._emit({"type": "result", "ok": ok, "outputs": outputs or [],
                           "summary": summary or {}, "error_code": error_code,
                           "message": message, "retryable": retryable,
                           "elapsed_s": round(time.time() - self.t0, 1)})

    # ---- 步骤 ----
    def step(self, sid, name, state, detail=None, error_code=None, elapsed_s=None):
        self._poll_window()          # 与 log 同款安全点(需求 F: 等待环节也能及时响应开关)
        obj = {"type": "step", "id": sid, "name": name, "state": state}
        if detail:
            obj["detail"] = detail
        if error_code:
            obj["error_code"] = error_code
        if elapsed_s is not None:
            obj["elapsed_s"] = elapsed_s
        return self._emit(obj)

    def step_start(self, sid, name, detail=None):
        return self.step(sid, name, "running", detail)

    def step_ok(self, sid, name, detail=None, elapsed_s=None):
        return self.step(sid, name, "ok", detail, elapsed_s=elapsed_s)

    def step_skip(self, sid, name, detail=None):
        return self.step(sid, name, "skipped", detail)

    def step_fail(self, sid, name, detail, error_code, elapsed_s=None):
        return self.step(sid, name, "failed", detail, error_code=error_code, elapsed_s=elapsed_s)

    # ---- 人工操作 ----
    def need_human(self, action, message, platform=None, timeout_s=300, choices=None,
                   context=None, free_text=False):
        """context: 卡点详情(platform/store/date/step/error_code/title/hint/error/shot/attempt),
        供客户端渲染失败面板; free_text: 是否允许用户补充描述(P1 用)。"""
        # 2026-10-13 后台模式: 登录/短信这类"要人在浏览器里操作"的请求 → 自动把窗口弹回主屏
        if action in ("login", "sms_code"):
            try:
                from . import browser_window as _bw
                _bw.show(self, reason=("登录/滑块验证" if action == "login" else "短信验证码"))
            except Exception:
                pass
        obj = {"type": "need_human", "action": action, "message": message, "timeout_s": timeout_s}
        if platform:
            obj["platform"] = platform
        if choices:
            obj["choices"] = choices
        if context:
            obj["context"] = context
        if free_text:
            obj["free_text"] = True
        return self._emit(obj)

    def browser_hide(self):
        """后台模式: 把浏览器窗口收回屏幕外(登录/人工环节结束时调用; 幂等, 无后台时为空操作)。"""
        try:
            from . import browser_window as _bw
            _bw.hide(self)
        except Exception:
            pass

    # ---- 日志/进度 ----
    def _poll_window(self):
        """窗口开关**安全点**(需求 F): 「展示过程」的滑入/滑出在这里落地。

        为什么挂 log/step: 这两个是本进程**最密集**的公共调用点 —— 挂在它们身上就等于
        任务每走一小步都会给主线程一次机会处理窗口动作(响应速度 ~1 秒级), 不用在每个
        平台循环里各处埋点。未启用/无待办时只是一次属性读取(零成本); 失败静默。
        ⚠️ 只许在主线程执行(Playwright sync API 绑定主线程) —— poll() 内部自己判定。
        """
        try:
            from . import browser_window as _bw
            _bw.poll(self)
        except Exception:
            pass

    def log(self, msg, level="info"):
        self._poll_window()
        return self._emit({"type": "log", "level": level, "msg": msg})

    def progress(self, step, pct):
        return self._emit({"type": "progress", "step": step, "pct": max(0, min(100, int(pct)))})

    # ---- LLM 流式 ----
    def llm_delta(self, scope, text):
        return self._emit({"type": "llm_delta", "scope": scope, "text": text})

    def llm_done(self, scope):
        return self._emit({"type": "llm_done", "scope": scope})

    def llm_suggestion(self, step, patch, message):
        return self._emit({"type": "llm_suggestion", "scope": "self_heal", "step": step,
                           "patch": patch, "message": message})

    def close(self):
        if self._echo_fp:
            self._echo_fp.close()


# 人工等待的默认上限(秒): 无人应答时的兜底。调用方已有各自的"无人值守"回退语义
# (继续/跳过/结束), 此前问题是 readline 永不返回、那些兜底永远到不了, 于是内核卡死。
DEFAULT_WAIT_TIMEOUT_S = 900          # 15 分钟


class HumanBridge:
    """need_human 的响应来源。壳模式: 从**常驻 stdin 分发器**取(带超时兜底); CLI 模式: callback/终端。"""

    def __init__(self, from_stdin=False, cli_callback=None):
        self.from_stdin = from_stdin
        self.cli_callback = cli_callback

    def wait(self, need_human_obj, timeout_s=None):
        """等待人工响应, 返回 dict 或 None(超时/通道不可用)。

        2026-10-13 修复"卡死内核"缺口(客户机残留内核事故的机制根因): 此前 from_stdin 是
        **裸 readline 无限阻塞**(注释写"壳负责超时", 但壳层从未实现) —— 用户不点面板时,
        内核永久卡在等待里 = 残留内核 + 后续全被"另一个任务在运行"挡住。
        超时 → 返回 None → 调用方既有的"无人值守"回退逻辑接管(要么跳继续、要么保守结束,
        绝不是永远卡着)。内核侧默认上限 900s; 壳侧另按事件的 timeout_s 自动应答(更快)。

        2026-10-14(需求 F): stdin 上现在跑**多路消息**(人工响应 + 「展示过程」开关), 临时
        readline 线程会跟窗口开关抢行/丢消息 → 改为从**常驻分发器**(stdin_router)的队列取。
        单读者红线: 本方法**不再自己开 readline 线程**; 超时语义与原实现完全一致
        (队列 get(timeout=limit), 超时返回 None)。
        """
        if self.cli_callback:
            return self.cli_callback(need_human_obj)
        if self.from_stdin:
            import queue
            from . import stdin_router
            limit = DEFAULT_WAIT_TIMEOUT_S if timeout_s is None else float(timeout_s)
            router = stdin_router.current()
            if router is None:
                # 壳模式入口(task_main)必然会启动分发器; 没启动=调用方没走正规入口。
                # 兜底: 就地启动一个(幂等), 保证"等人工"这条路永远有人在读 stdin。
                router = stdin_router.start()
            else:
                router.start()      # 幂等: 已有活着的读取线程时直接返回
            q = router.human_queue
            try:
                obj = q.get(timeout=limit) if limit and limit > 0 else q.get(block=False)
            except queue.Empty:
                return None                     # 超时 → 与"通道不可用"同语义, 调用方兜底
            if not isinstance(obj, dict):
                return None
            return obj
        return None
