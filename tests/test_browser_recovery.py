# -*- coding: utf-8 -*-
"""浏览器"被连续关闭"的恢复路径修复单测 (2026-10-12 客户机实证)。

现场: 客户机界面 = "自动化浏览器被连续关闭(已自动重开 2 次仍失败)" +
      "Locator.count: Target page, context or browser has been closed
       Browser logs: <launching> ...chrome.exe" (新包, 已知收尾文案)。

代码侧确定的缺陷链: Windows 分支"杀残留进程"后**不等退出**就立刻启动新浏览器
→ 被杀的旧进程还没释放 profile → 新 Chromium 起来就死 → "自动重开"连开连死,
把一次死亡放大成"连续关闭"。修复三件套 + 取证:
  ① 杀进程序列 = 顶层(命令行含 profile) + 全部后代(renderer/gpu 子进程可能不带 profile);
  ② 杀完 wait_profile_free 等到真正释放才返回;
  ③ 重开后 wait_browser_ready 验证真的活着, 没活就关掉再试一次;
  ④ browser_death_forensics 落一份"死亡现场"文件(进程表+崩溃事件), 供区分外部杀/自身崩。
"""
import inspect
import os
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "kernel"))
from merchant_report.platforms import taobao as T   # noqa: E402


def test_select_kill_set():
    prof = r"C:\Users\demo\AppData\Roaming\MerchantReportClient\browser-profile"
    table = [
        (100, 1, "chrome.exe", rf'...\chrome.exe --user-data-dir="{prof}" --disable-blink-features'),
        (101, 100, "chrome.exe", r'...\chrome.exe --type=renderer'),   # 后代, 命令行里无 profile
        (102, 101, "chrome.exe", r'...\chrome.exe --type=utility'),    # 孙代
        (200, 1, "python.exe", r'...\python.exe merchant_report.task_main'),   # 无关
        (300, 200, "cmd.exe", r'...\cmd.exe'),                         # 无关的后代
        (400, 1, "msedgewebview2.exe",
         '--embedded-browser-webview=1 --user-data-dir="' + prof + '"'),
        # ↑ 系统组件 WebView: 哪怕命令行出现 profile 也绝不杀(2026-10-13 客户机报告)
        (500, 1, "powershell.exe", 'powershell -c "Get-Item ' + prof + r'\x.txt"'),
        # ↑ 命令行含 profile 的非浏览器(对照实验的测试运行器就这样被误杀过, 实测两次)
    ]
    got = T.select_profile_kill_pids(table, prof)
    assert got == [100, 101, 102], got
    # 大小写不敏感(Windows 路径)
    got2 = T.select_profile_kill_pids(
        [(7, 1, "CHROME.EXE", 'CHROME.EXE --USER-DATA-DIR=' + prof.upper())], prof)
    assert got2 == [7], got2
    print("✅ 杀进程序列: 家族+profile 命中顶层与全部后代; webview/非浏览器绝不碰")


def test_wait_profile_free():
    seq = {"n": 0}

    def probe(_p):
        seq["n"] += 1
        return [123] if seq["n"] < 3 else []
    ok, left = T.wait_profile_free("X", timeout_s=5, poll_s=0.01, probe=probe)
    assert ok and left == 0 and seq["n"] == 3, (ok, left, seq)
    ok2, left2 = T.wait_profile_free("X", timeout_s=0.05, poll_s=0.01, probe=lambda p: [1, 2])
    assert (not ok2) and left2 == 2
    ok3, left3 = T.wait_profile_free("X", timeout_s=0.05, poll_s=0.01, probe=lambda p: None)
    assert (not ok3) and left3 == -1
    print("✅ wait_profile_free: 等到释放 / 超时报数 / 探测不可用, 三种情形都对")


def test_wait_browser_ready():
    class DeadCtx:
        @property
        def pages(self):
            raise RuntimeError("Target closed")

    class AliveCtx:
        class P:
            def evaluate(self, *_a):
                return 1

        @property
        def pages(self):
            return [self.P()]

    assert T.wait_browser_ready(DeadCtx(), timeout_s=0.3, poll_s=0.05) is False
    assert T.wait_browser_ready(AliveCtx(), timeout_s=0.5, poll_s=0.05) is True
    print("✅ wait_browser_ready: 死 context=False, 活 context=True")


def test_forensics_file():
    import re
    with tempfile.TemporaryDirectory() as d:
        p = T.browser_death_forensics(d, d, err_text="Target closed 测试现场")
        assert p and os.path.exists(p), p
        txt = open(p, encoding="utf-8").read()
        for kw in ("浏览器死亡现场", "Target closed 测试现场", "相关进程", "Application 错误事件", "被外部结束"):
            assert kw in txt, (kw, txt[:300])
        assert re.search(r"browser_diag_\d{8}_\d{6}\.txt$", os.path.basename(p))
    print("✅ 取证文件: 生成且含全部关键段落")


def test_format_diag_filter():
    rows = [(1, 0, "chrome.exe", r"C:\x\chrome.exe --type=renderer"),
            (2, 0, "notepad.exe", r"C:\x\notepad.exe")]
    txt = T.format_browser_diag("now", "err", "prof", rows, ["evt-line"], "Windows")
    assert "chrome.exe" in txt and "notepad.exe" not in txt and "evt-line" in txt
    # 空列表=查询成功但无记录; None=查询失败/未查询 → 两种判词必须分得开(2026-10-12 判读教训)
    t2 = T.format_browser_diag("now", "err", "prof", rows, [], "Windows")
    assert "无崩溃事件记录" in t2, t2
    t3 = T.format_browser_diag("now", "err", "prof", rows, None, "Windows")
    assert "读取失败" in t3 and "无崩溃事件记录" not in t3, t3
    # Crashpad 转储段(2026-10-13 报告: 无事件条目≠被外部结束; 有 dump 即进程内崩溃)
    t4 = T.format_browser_diag("now", "err", "prof", rows, [], "Windows",
                               dumps=[("abc123.dmp", 3.2)])
    assert "进程内崩溃" in t4 and "abc123.dmp" in t4, t4[-400:]
    t5 = T.format_browser_diag("now", "err", "prof", rows, [], "Windows")
    assert "无近期 dump" in t5, t5[-300:]
    print("✅ 取证格式: 家族过滤+事件判别保留; dump 有/无两判词分明")


def test_parse_proc_table():
    import json as J
    rows = [{"ProcessId": 100, "ParentProcessId": 1, "Name": "chrome.exe",
             "CommandLine": r'C:\x\chrome.exe --user-data-dir="P"'},
            {"ProcessId": 101, "ParentProcessId": 100, "Name": "chrome.exe",
             "CommandLine": r'C:\x\chrome.exe --type=renderer'}]
    mk = lambda body: ("__TBL_OK__\n" + body).encode("utf-8")
    # ① utf-8 正常(字段: pid, ppid, name, cmdline)
    got = T._parse_proc_table(mk(J.dumps(rows, ensure_ascii=False)))
    assert got == [(100, 1, "chrome.exe", rows[0]["CommandLine"]),
                   (101, 100, "chrome.exe", rows[1]["CommandLine"])], got
    # ② 单对象(只有一条时 ConvertTo-Json 不套数组)
    one = T._parse_proc_table(mk(J.dumps(rows[0], ensure_ascii=False)))
    assert one == [(100, 1, "chrome.exe", rows[0]["CommandLine"])], one
    # ③ 中文路径 GBK 字节也要能解(中文 Windows 常见); 缺 Name 字段 → ""(防御)
    gbk_rows = [{"ProcessId": 7, "ParentProcessId": 1, "CommandLine": "C:\\360壁纸\\x.exe"}]
    g = T._parse_proc_table(("__TBL_OK__\n" + J.dumps(gbk_rows, ensure_ascii=False)).encode("gbk"))
    assert g == [(7, 1, "", "C:\\360壁纸\\x.exe")], g
    # ④ 空结果(无匹配) → []；没有哨兵(静默失败) → None；坏 JSON → None
    assert T._parse_proc_table(mk("")) == []
    assert T._parse_proc_table(mk("null")) == []
    assert T._parse_proc_table(b"") is None
    assert T._parse_proc_table(b"some error text, no marker") is None
    assert T._parse_proc_table(mk("not json {")) is None
    # ⑤ utf-16-le(无 BOM) 也要认
    u16 = ("__TBL_OK__\n" + J.dumps(rows, ensure_ascii=False)).encode("utf-16-le")
    assert T._parse_proc_table(u16) == [(100, 1, "chrome.exe", rows[0]["CommandLine"]),
                                        (101, 100, "chrome.exe", rows[1]["CommandLine"])]
    print("✅ 进程表解析: utf-8/gbk/utf-16 + 空/单条/静默失败/坏JSON 全对")


def test_cleanup_note():
    n = T._cleanup_note(-1, False, -1)
    assert n and "兜底" in n[0] and "-1" not in n[0], n
    n2 = T._cleanup_note(2, True, 0)
    assert n2 and "2 个" in n2[0] and "-1" not in n2[0], n2
    n3 = T._cleanup_note(2, False, 1)
    assert n3 and "仍有 1 个" in n3[0], n3
    n4 = T._cleanup_note(0, False, -1)
    assert n4 and n4[1] == "warn" and "-1" not in n4[0], n4
    assert T._cleanup_note(0, True, 0) is None
    print("✅ 清理文案: 兜底/正常/残留/探测不可用 —— 不再出现 \"-1 个\"")


def test_orphan_sweep():
    # ① 兜底清扫命令: 按**可执行文件路径**扫本程序浏览器目录(不碰用户自己的浏览器)
    ps = T._msplaywright_sweep_ps()
    assert "MerchantReportClient" in ps and "ms-playwright" in ps, ps
    assert "ExecutablePath" in ps, "要按可执行文件路径扫, 不许只按命令行(漏杀根源)"
    assert "Stop-Process" in ps and "-ne $PID" in ps, ps
    # ② 残留点名(纯函数): pid+命令行、超长截断、坏行跳过、limit 生效
    lines = T._survivor_lines([(123, 1, "chrome.exe", "C:\\x\\chrome.exe --type=renderer " + "x" * 300),
                               (124, 1, "chrome.exe", "C:\\y\\chrome.exe"),
                               ("bad",),
                               (125, 1, "third.exe", "C:\\z\\third.exe")])
    assert len(lines) == 2, f"坏行要跳过, limit=3 只取前 3 行(其中坏行被跳): {lines}"
    assert lines[0].startswith("↳ 仍占用: pid=123") and len(lines[0]) <= 140, lines[0]
    assert "pid=124" in lines[1], lines[1]
    # ③ 接线: 清理流程用上"升级清扫"与"残留点名"
    src = inspect.getsource(T._cleanup_stale_browser)
    assert "_msplaywright_sweep_ps" in src, "升级清扫未接线(残留链修复点)"
    assert "_survivor_lines" in src, "残留点名未接线"
    print("✅ 残留链: 升级清扫(按路径全量, 不碰用户浏览器) + 残留点名(pid+命令行) 都在位")


def test_selfmatch_and_survivor_filter():
    # ① 进程表/取证查询必须排除"探针自己"(否则每次探测都把自己快照进去 → 等释放永远等不满)
    ps = T._build_proc_table_ps("C:\\Users\\x\\AppData\\Roaming\\MerchantReportClient\\browser-profile")
    assert "-ne $PID" in ps, "进程表查询未排除自身(自伤: '仍有1个未退出'永远清不掉)"
    assert "browser-profile" in ps and "__TBL_OK__" in ps, ps
    assert "msedgewebview2" in ps, "家族判定要排除 msedgewebview2(系统组件 WebView)"
    assert "Name" in ps, "查询要带 Name 字段(家族判定/点名展示都靠它)"
    assert "-ne $PID" in T._build_proc_table_ps(), "无 profile 时同样要排除自身"
    assert "-ne $PID" in inspect.getsource(T.browser_death_forensics), "死亡取证查询也要排除自身"
    # ② 残留点名只列真占本 profile 的行(别家 WebView/别的目录/非浏览器不许出现)
    prof = "C:\\Users\\x\\AppData\\Roaming\\MerchantReportClient\\browser-profile"
    rows = [(1, 0, "chrome.exe", r'"C:\x\chrome.exe" --user-data-dir="C:\Users\x\AppData\Roaming\MerchantReportClient\browser-profile"'),
            (2, 0, "msedgewebview2.exe", r'"C:\Program Files (x86)\Microsoft\EdgeWebView\Application\154\msedgewebview2.exe" --embedded-browser-webview=1'),
            (3, 0, "other.exe", "C:\\x\\other.exe --user-data-dir=C:\\Other\\browser-profile"),
            (4, 0, "chrome.exe", r'"chrome.exe" --type=renderer --user-data-dir="C:\Users\x\AppData\Roaming\MerchantReportClient\browser-profile\Default"')]
    got = T.pick_profile_rows(rows, prof)
    assert [r[0] for r in got] == [1, 4], got
    # msedgewebview2 即使命令行出现本 profile 也不算(系统组件, 2026-10-13 报告)
    rows2 = rows + [(5, 0, "msedgewebview2.exe",
                     r'--user-data-dir="C:\Users\x\AppData\Roaming\MerchantReportClient\browser-profile"')]
    assert [r[0] for r in T.pick_profile_rows(rows2, prof)] == [1, 4]
    assert T.pick_profile_rows(rows, prof, limit=1) == [rows[0]]
    assert T.pick_profile_rows(rows, "") == [], "空 profile → 一律不列(防御)"
    # ③ 接线: 清理流程点名走过滤函数
    assert "pick_profile_rows" in inspect.getsource(T._cleanup_stale_browser), "点名前必须按 profile 过滤"
    print("✅ 自伤防护+家族口径: 排除自身/webview/非浏览器; 点名只列真占 profile 的浏览器进程")


def test_wiring_source():
    src_cl = inspect.getsource(T._cleanup_stale_browser)
    assert "_other_live_task_pids" in src_cl, "双开防护闸不许丢(CI 冒烟同查)"
    assert "wait_profile_free" in src_cl, "杀完必须等 profile 真正释放(本次修复点)"
    assert "select_profile_kill_pids" in src_cl, "要按'顶层+后代'序列杀"
    assert "_cleanup_note" in src_cl, "收尾文案走纯函数(不许再打出 '-1 个')"
    assert "time.sleep(3)" in src_cl, "探测不可用时固定等待兜底"
    from merchant_report import orchestrator as O
    src_rt = inspect.getsource(O.run_task)
    assert "wait_browser_ready" in src_rt, "重开必须校验'真的活着'"
    assert "browser_death_forensics" in src_rt, "首次死亡要落现场取证"
    assert "watch_browser" in src_rt, "要挂断开监听"
    print("✅ 接线检查: 清理等待 + 重开校验 + 现场取证 + 断开监听 都在位")


def main():
    test_select_kill_set()
    test_wait_profile_free()
    test_wait_browser_ready()
    test_forensics_file()
    test_format_diag_filter()
    test_parse_proc_table()
    test_cleanup_note()
    test_orphan_sweep()
    test_selfmatch_and_survivor_filter()
    test_wiring_source()
    print("\n✅ 浏览器恢复路径(Windows 竞态修复+取证+探测加固)全绿")


if __name__ == "__main__":
    main()
