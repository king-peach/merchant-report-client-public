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
        return self._emit({"type": "result", "ok": ok, "outputs": outputs or [],
                           "summary": summary or {}, "error_code": error_code,
                           "message": message, "retryable": retryable,
                           "elapsed_s": round(time.time() - self.t0, 1)})

    # ---- 步骤 ----
    def step(self, sid, name, state, detail=None, error_code=None, elapsed_s=None):
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

    # ---- 日志/进度 ----
    def log(self, msg, level="info"):
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
    """need_human 的响应来源。壳模式: 从 stdin 读(**带超时兜底**); CLI 模式: 从 callback/终端读。"""

    def __init__(self, from_stdin=False, cli_callback=None):
        self.from_stdin = from_stdin
        self.cli_callback = cli_callback

    def wait(self, need_human_obj, timeout_s=None):
        """等待人工响应, 返回 dict 或 None(超时/通道不可用)。

        2026-10-13 修复"卡死内核"缺口(客户机残留内核事故的机制根因): 此前 from_stdin 是
        **裸 readline 无限阻塞**(注释写"壳负责超时", 但壳层从未实现) —— 用户不点面板时,
        内核永久卡在等待里 = 残留内核 + 后续全被"另一个任务在运行"挡住。
        现在: 读行放 daemon 线程 + 队列超时(Windows 管道没有 select, 只能这么等);
        超时 → 返回 None → 调用方既有的"无人值守"回退逻辑接管(要么跳继续、要么保守结束,
        绝不是永远卡着)。内核侧默认上限 900s; 壳侧另按事件的 timeout_s 自动应答(更快)。
        """
        if self.cli_callback:
            return self.cli_callback(need_human_obj)
        if self.from_stdin:
            import queue
            import threading
            limit = DEFAULT_WAIT_TIMEOUT_S if timeout_s is None else float(timeout_s)
            q = queue.Queue()

            def _reader():
                try:
                    q.put(sys.stdin.readline())
                except Exception:
                    q.put("")

            threading.Thread(target=_reader, daemon=True).start()
            try:
                line = q.get(timeout=limit) if limit and limit > 0 else q.get(block=False)
            except queue.Empty:
                return None                     # 超时 → 与"通道不可用"同语义, 调用方兜底
            if not line:
                return None
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None
        return None
