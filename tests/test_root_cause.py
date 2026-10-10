# -*- coding: utf-8 -*-
"""根因定位(确定性)单测: 已知原因必须被认出来, 且不得退化成"多等几秒"

覆盖: 浏览器被关闭 / 配置目录占用 / 平台未出报表 / 网络 / 登录失效 / 页面改版 / 加载慢 / 未知
"""
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "kernel"))

from merchant_report.diagnose import classify, root_cause

CASES = [
    # (现场错误, 异常类型, 期望 id, 期望 code, 期望动作, 是否可自动恢复)
    ("BrowserContext.new_page: Target page, context or browser has been closed",
     "TargetClosedError", "browser_closed", "E_BROWSER_DEAD", "relaunch", True),
    ("Browser has been closed", "Error", "browser_closed", "E_BROWSER_DEAD", "relaunch", True),
    ("Failed to launch: ProcessSingleton lock: user data directory is already in use",
     "Error", "profile_locked", "E_PROFILE_LOCK", "relaunch", True),
    ("美团报表无可用数据行(下载或解析失败)", "", "no_report", "E_NO_REPORT", "retry", False),
    ("net::ERR_CONNECTION_RESET 无法访问", "Error", "network", "E_NETWORK", "retry", False),
    ("美团报表页未就绪(可能登录态失效): https://...", "", "login_expired", "E_AUTH", "relogin", False),
    ("未匹配到元素 selector=.report-download-btn", "Error", "page_changed", "E_STRUCTURAL", "diagnose", False),
    ("Timeout 30000ms exceeded while waiting for selector", "TimeoutError",
     "slow_load", "E_TRANSIENT", "retry", False),
    ("完全看不懂的异常 xyz-123", "WeirdError", "unknown", "E_UNKNOWN", "retry", False),
]

print("=== 1. 根因判定表 ===")
for err, exc, want_id, want_code, want_act, want_auto in CASES:
    rc = root_cause(err, exc)
    assert rc["id"] == want_id, f"{err[:40]} → {rc['id']} (期望 {want_id})"
    assert rc["code"] == want_code, (err, rc["code"])
    assert rc["action"] == want_act, (err, rc["action"])
    assert rc.get("auto_recover", False) is want_auto, (err, rc.get("auto_recover"))
    assert rc["title"] and rc["hint"] and rc["choices"], rc
    print(f"  {want_id:14s} ← {(exc or '-'):17s} {err[:52]}")
    print(f"    → 「{rc['title']}」 动作={rc['action']} 自动恢复={rc.get('auto_recover')}")

print("=== 2. 浏览器关闭必须能自动恢复(不能只弹窗多等) ===")
rc = root_cause("Target page, context or browser has been closed", "TargetClosedError")
assert rc["auto_recover"] is True and rc["action"] == "relaunch"
assert "重开浏览器并继续" in rc["choices"]
print(f"  动作 ✓ {rc['action']} / 选项 ✓ {rc['choices']}")

print("=== 3. 未知原因不得给出等待型结论 ===")
rc = root_cause("完全看不懂的异常 xyz-123", "WeirdError")
assert rc["id"] == "unknown" and rc["action"] == "retry"
assert "诊断包" in " ".join(rc["choices"]), rc["choices"]
print(f"  未知 → 动作 {rc['action']}, 提供升级路径 ✓ {rc['choices']}")

print("=== 4. 老接口 classify 兼容(编排层/其他调用方仍在用) ===")
for err, _, want_id, want_code, _, _ in CASES:
    c = classify(err)
    assert c.startswith("E_"), c
print(f"  classify 仍返回错误码 ✓ 例: {classify(CASES[0][0])}")

print("\n✅ 根因定位单测全部通过")
