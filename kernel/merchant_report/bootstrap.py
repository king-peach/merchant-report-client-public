# -*- coding: utf-8 -*-
"""浏览器组件引导: 首次运行确保 Playwright 自带 Chromium 可用。

为什么不把 Chromium 打进安装包: 打进去安装包达 **251MB**, 超过 GitHub 单文件 100MB 硬限制
(推 build 分支被 pre-receive hook 拒), 且用户下载/更新都更重。
改为首次运行时从**国内镜像**下载(约 130MB)到用户数据目录, 引擎仍是 Playwright 官方 Chromium
—— 与 macOS 同一引擎, 行为一致。
下载失败**不阻塞任务**: launch_browser 会按 [自带Chromium → msedge → chrome] 回退。
"""
import os
import re
import subprocess
import sys
import time

DEFAULT_MIRROR = "https://cdn.npmmirror.com/binaries/playwright"
DOWNLOAD_TIMEOUT_S = 1800


def browsers_dir():
    """Windows 客户机: 统一放用户数据目录(不打进安装包); 其他平台用 Playwright 默认缓存。"""
    from .config import app_data_dir
    return app_data_dir() / "ms-playwright"


def prepare_env(mirror=None):
    """设置 PLAYWRIGHT_BROWSERS_PATH(仅 Windows, 除非已由壳/用户指定)。"""
    if os.name == "nt" and not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir())
    return os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")


def chromium_path():
    """返回自带 Chromium 可执行文件路径(不存在则空串)。"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
        return exe if exe and os.path.exists(exe) else ""
    except Exception:
        return ""


def _verify_marker(exe):
    """自检标记: 同一可执行文件只需自检一次(避免每次任务多花几秒)。
    换版本/换路径会让标记失效, 自动重验。"""
    from .config import app_data_dir
    return app_data_dir() / ".browser_verified"


def _verify_cached(exe, sink):
    """返回 (ok, why)。已验过且路径未变 → 直接放行; 24h 内验过失败的也不再重验。"""
    mark = _verify_marker(exe)
    try:
        if mark.exists() and mark.read_text(encoding="utf-8").strip() == exe:
            return True, ""
    except Exception:
        pass
    from .browser_pref import mark_verify_failed, verify_failed_recently
    if verify_failed_recently():
        return False, "此前自检失败(24 小时内不再重复自检)"
    from .platforms.taobao import verify_browser
    sink.log("🔍 正在自检浏览器组件(首次使用该版本时验一次)…")
    ok, why = verify_browser(exe)
    if ok:
        try:
            mark.write_text(exe, encoding="utf-8")
        except Exception:
            pass
        return True, ""
    # 自检失败: 只记"验过了失败"(24h 内不再重复白等 45s), **不写 bundled_broken**
    # —— headless 自检失败 ≠ 有头启动失败(曾因一次偶发超时把记忆写成"内置 Chromium 不可用",
    #    于是全走系统 Chrome, 而 Chrome 152+ 会忽略我们的下载目录设置 → 报表下到别处)。
    # 真正的"不可用"只由 launch_browser 的实际启动结果来记。
    mark_verify_failed(why)
    return False, why


def ensure_browser(sink, mirror=None, timeout_s=DOWNLOAD_TIMEOUT_S):
    """确保 Chromium 可用。返回 (ok, exe_or_reason)。首次运行会下载, 失败只告警不抛。
    下载过程**实时回报进度**(百分比 + 阶段行), 否则界面几分钟没动静, 用户会以为卡死。
    已存在时**自检一次**(杀软拦截会表现为"文件在但启动超时", 早发现早降级)。"""
    bdir = prepare_env(mirror)
    exe = chromium_path()
    if exe:
        ok_v, why = _verify_cached(exe, sink)
        if ok_v:
            sink.log(f"🌐 自带 Chromium 已就绪" + (f"（{bdir}）" if bdir else ""))
            return True, exe
        sink.log("⚠️ 自带 Chromium **自检未通过**（不判定为不可用：仍会先试有头启动，失败再回退系统浏览器）", level="warn")
        sink.log(f"   原因: {why[:200]}", level="warn")
        return False, f"自检未通过: {why[:120]}"      # 已有浏览器, 不重新下载
    host = (mirror or os.environ.get("PLAYWRIGHT_DOWNLOAD_HOST") or DEFAULT_MIRROR)
    env = dict(os.environ)
    env["PLAYWRIGHT_DOWNLOAD_HOST"] = host
    sink.log(f"⬇️ 首次运行需下载浏览器组件(约 130MB, **自动下载, 无需你手动操作**, 只需一次)")
    sink.log(f"   镜像: {host}")
    if bdir:
        sink.log(f"   安装位置: {bdir}")
    try:
        proc = subprocess.Popen([sys.executable, "-m", "playwright", "install", "chromium"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
    except Exception as e:
        sink.log(f"⚠️ 无法启动下载进程: {str(e)[:100]}——本次将回退系统 Edge/Chrome", level="warn")
        return False, str(e)[:100]

    t0 = time.time()
    last_pct, last_report, lines_shown = -10, 0.0, 0
    buf = ""
    try:
        stream = proc.stdout
        while True:
            chunk = stream.read(256) if stream else ""
            if not chunk:
                break
            buf = (buf + chunk)[-4000:]
            # 百分比进度(playwright 用 \\r 刷新同一行) —— 每跨 10% 或每 5 秒报一次
            pcts = re.findall(r"(\d{1,3})%", buf)
            if pcts:
                pct = int(pcts[-1])
                if pct // 10 != last_pct // 10 or (time.time() - last_report) > 5:
                    sink.log(f"   ⬇️ 下载中… {pct}%")
                    last_pct, last_report = pct, time.time()
            # 阶段行(下载地址/解压/完成) 只回显前几条, 避免刷屏
            for line in re.split(r"[\r\n]+", buf):
                line = line.strip()
                if line and "%" not in line and len(line) > 8 and lines_shown < 4:
                    sink.log(f"   {line[:150]}")
                    lines_shown += 1
            if time.time() - t0 > timeout_s:
                proc.kill()
                sink.log(f"⚠️ 下载超时({timeout_s}s)——本次将回退系统 Edge/Chrome", level="warn")
                return False, "下载超时"
        rc = proc.wait(timeout=60)
        if rc != 0:
            sink.log(f"⚠️ 浏览器组件下载失败(退出码 {rc})——本次将回退系统 Edge/Chrome", level="warn")
            return False, f"install 退出码 {rc}"
    except Exception as e:
        try:
            proc.kill()
        except Exception:
            pass
        sink.log(f"⚠️ 浏览器组件下载异常: {str(e)[:100]}——本次将回退系统 Edge/Chrome", level="warn")
        return False, str(e)[:100]

    exe = chromium_path()
    if exe:
        # 下载后立刻自检一次并记标记: 杀软拦截/下载损坏会表现为"文件在但启动超时",
        # 早发现早告知, 好过让用户等 3 分钟超时还不知道原因
        ok_v, why = _verify_cached(exe, sink)
        if ok_v:
            sink.log(f"✅ 浏览器组件已就绪（耗时 {int(time.time() - t0)}s，以后不再下载）")
            return True, exe
        sink.log("⚠️ 浏览器组件下载完成但**启动失败**——本次将回退系统 Edge/Chrome", level="warn")
        sink.log(f"   原因: {why[:220]}", level="warn")
        sink.log("   若反复出现: 把本程序与 %APPDATA%\\MerchantReportClient 加入安全软件白名单", level="warn")
        return False, f"自检失败: {why[:120]}"
    sink.log("⚠️ 下载完成但未找到 Chromium 可执行文件——回退系统 Edge/Chrome", level="warn")
    return False, "下载后仍未找到可执行文件"
