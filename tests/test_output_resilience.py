# -*- coding: utf-8 -*-
"""输出故障鲁棒性单测: stdout 挂掉时事件不能丢, 更要紧的是——真实错误不能被顶掉。

背景(Windows 实测): 内核 stdout 写入抛 OSError(EINVAL) 时会盖住真正的失败原因,
用户在界面上只看到 "OSError: [Errno 22] Invalid argument"，无从排查。
"""
import io
import json
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "kernel"))

from merchant_report.events import EventSink


class BrokenStdout:
    """模拟 Windows 上管道读端关闭: 任何写入都抛 OSError(EINVAL)"""

    def write(self, s):
        raise OSError(22, "Invalid argument")

    def flush(self):
        raise OSError(22, "Invalid argument")


class BadEncodingStdout:
    """模拟 cp936 stdout: 中文写入抛 UnicodeEncodeError"""

    def __init__(self):
        self.buf = []

    def write(self, s):
        try:
            s.encode("ascii")
        except UnicodeEncodeError as e:
            raise e
        self.buf.append(s)

    def flush(self):
        pass


def capture_stderr(fn):
    old = sys.stderr
    sys.stderr = io.StringIO()
    try:
        fn()
        return sys.stderr.getvalue()
    finally:
        sys.stderr = old


print("=== 1. stdout 抛 OSError(EINVAL): 事件不丢, 降级到 stderr ===")
real_stdout = sys.stdout
sink = EventSink("t1", to_stdout=True)
sink._fallback_path = _P("/tmp/gj_events_fallback_test.ndjson")   # 隔离, 不写用户数据目录
if sink._fallback_path.exists():
    sink._fallback_path.unlink()
sys.stdout = BrokenStdout()
try:
    err = capture_stderr(lambda: (
        sink.log("普通日志"),
        sink.result(False, error_code="E_CRASH", message="真正的错误原因在这里"),
    ))
finally:
    sys.stdout = real_stdout

assert "stdout 写入失败" in err, err[:200]
assert "真正的错误原因在这里" in err, "真实错误必须出现在 stderr(壳会展示)"
lines = [json.loads(x) for x in sink._fallback_path.read_text(encoding="utf-8").splitlines()]
kinds = [x["type"] for x in lines]
assert "log" in kinds and "result" in kinds, kinds
res = [x for x in lines if x["type"] == "result"][0]
assert res["error_code"] == "E_CRASH" and "真正的错误原因在这里" in res["message"]
print(f"  ✓ stderr 降级提示 + 落盘 {len(lines)} 条事件(含 result/E_CRASH)")
print(f"  ✓ 真实错误完整保留: {res['message']}")

print("=== 2. 已标记损坏后继续发射: 不再重复提示, 但仍落盘 ===")
sys.stdout = BrokenStdout()
try:
    err2 = capture_stderr(lambda: sink.log("第二条(应静默降级)"))
finally:
    sys.stdout = real_stdout
assert "stdout 写入失败" not in err2, "不该重复刷屏提示"
assert "第二条" in err2, err2
n2 = len(sink._fallback_path.read_text(encoding="utf-8").splitlines())
assert n2 == len(lines) + 1, (n2, len(lines))
print(f"  ✓ 无重复提示, 事件条数 {len(lines)} → {n2}")

print("=== 3. 编码异常(UnicodeEncodeError)同样不会中断任务 ===")
sink2 = EventSink("t2", to_stdout=True)
sink2._fallback_path = _P("/tmp/gj_events_fallback_test2.ndjson")
if sink2._fallback_path.exists():
    sink2._fallback_path.unlink()
sys.stdout = BadEncodingStdout()
try:
    capture_stderr(lambda: sink2.log("中文日志 emoji 🎉"))
finally:
    sys.stdout = real_stdout
text = sink2._fallback_path.read_text(encoding="utf-8")
assert "中文日志 emoji 🎉" in text, text
print("  ✓ 编码失败也走了降级落盘, 中文内容无损")

print("=== 4. 正常 stdout 行为不变(不误伤开发机/CLI) ===")
buf = io.StringIO()
sys.stdout = buf
try:
    s3 = EventSink("t3", to_stdout=True)
    s3.log("正常一行")
finally:
    sys.stdout = real_stdout
out = buf.getvalue()
assert out.strip() and json.loads(out.strip())["msg"] == "正常一行"
assert s3._stdout_broken is False and s3._fallback_path is None
print(f"  ✓ 正常路径直写 stdout, 未触发降级")

print("=== 5. task_main 强制 UTF-8(Windows 根因之一) ===")
from merchant_report.task_main import _ensure_utf8_streams
buf2 = io.TextIOWrapper(io.BytesIO(), encoding="cp936", errors="strict")
old = sys.stdout
sys.stdout = buf2
try:
    _ensure_utf8_streams()
    enc = sys.stdout.encoding
finally:
    sys.stdout = old
assert enc.lower().replace("-", "") == "utf8", enc
print(f"  ✓ 启动时重配为 {enc}(Windows 默认 cp936 会导致中文事件流写出 GBK 字节)")

print("\n✅ 输出故障鲁棒性单测全部通过")
