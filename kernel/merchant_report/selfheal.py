# -*- coding: utf-8 -*-
"""自愈引擎: 自动化步骤失败 → LLM 诊断(页面上下文+错误) → 白名单补丁建议 → 壳确认 → 应用重试。
安全边界: 补丁只允许 选择器/文本/等待参数 三类, 绝不生成可执行代码。"""
import json
import re

# 白名单: 补丁字段 → 校验
PATCHABLE = {
    "selector": r"^.{1,200}$",              # 新选择器(Playwright/CSS)
    "nav_text": r"^[\u4e00-\u9fa5A-Za-z0-9_]{1,20}$",  # 导航/按钮文本
    "wait_ms": r"^\d{2,6}$",                # 等待毫秒
}


def collect_failure_context(page, error: str, step: str, exc_type: str = "",
                            extra: dict = None) -> dict:
    """失败现场: 错误 + 浏览器状态 + 页面文本片段(供 LLM 判断页面结构变化)。
    证据越全, LLM 越不容易靠"多等几秒"糊弄。"""
    ctx = {"step": step, "error": error[:300], "exc_type": exc_type or "",
           "url": "", "page_text": "", "ctx_alive": None, "pages": None}
    try:
        ctx["url"] = page.url[:200]
        txt = page.evaluate("() => document.body ? document.body.innerText : ''")
        # 压缩: 去空行/限长(LLM 无需全文, 要结构线索)
        lines = [l.strip() for l in txt.split("\n") if l.strip()]
        ctx["page_text"] = "\n".join(lines[:120])[:3500]
    except Exception as e:
        ctx["page_text"] = f"(页面读取失败: {e})"
        ctx["ctx_alive"] = False
    try:
        pages = getattr(page.context, "pages", None)
        if pages is not None:
            ctx["pages"] = len([p for p in pages if not p.is_closed()])
            ctx["ctx_alive"] = True if ctx["ctx_alive"] is None else ctx["ctx_alive"]
    except Exception:
        pass
    if extra:
        ctx.update(extra)
    return ctx


DIAG_PROMPT = """你是浏览器自动化故障定位专家。下面是一次失败的结构化现场证据。请先「定位」再下结论；证据不足时不要猜。

失败步骤: {step}
原始错误: {error}
异常类型: {exc_type}
浏览器上下文是否存活: {ctx_alive}
当前打开的页面数: {pages}
当前 URL: {url}
页面可见文本(前120行; 若显示"页面读取失败"说明页面已不可读):
---
{page_text}
---
候选可点击文本: {candidates}

只输出一个 JSON 对象(不要其他文字):
{{"cause": "browser_closed|profile_locked|login_expired|slow_load|page_changed|network|unknown",
  "evidence": "你依据的现场证据(引用上面哪一条)",
  "action": "relaunch|relogin|retry|wait|diagnose|skip",
  "patch": {{"nav_text": "..."}} 或 {{"selector": "..."}} 或 {{"wait_ms": "..."}} 或 {{}},
  "explain": "给商家看的白话说明",
  "confidence": "high|medium|low"}}

硬性规则:
1. 只有证据明确指向"页面元素/文本变了"才可给 page_changed; 只有证据明确指向"页面在加载中/网络抖动"才可给 slow_load + wait。
2. 页面文本读不到 且 上下文存活未知 → cause=unknown, action=diagnose, patch 留空。不要给 wait。
3. 严禁把"多等几秒"当兜底结论; 拿不准就 unknown + diagnose。前一次同样的错误用过 wait 还没好的, 不得再给 wait。
4. patch 只允许 nav_text / selector / wait_ms 三个键, 且只在 action 需要时才给。
5. explain 面向不懂技术的商家, 不出现错误码/英文堆栈。"""


def diagnose(llm_client, ctx: dict, candidates: list = None) -> dict:
    """调 LLM 定位(仅在确定性规则认不出根因时)。返回 {cause, diagnosis, patch, ...} 或 {"error": ...}"""
    prompt = (DIAG_PROMPT.replace("{step}", ctx["step"])
                        .replace("{error}", ctx["error"])
                        .replace("{exc_type}", str(ctx.get("exc_type", "")))
                        .replace("{ctx_alive}", str(ctx.get("ctx_alive")))
                        .replace("{pages}", str(ctx.get("pages")))
                        .replace("{url}", ctx["url"])
                        .replace("{page_text}", ctx.get("page_text", ""))
                        .replace("{candidates}", json.dumps(candidates or [], ensure_ascii=False)))
    try:
        raw = llm_client.chat([{"role": "system", "content": "只输出JSON。"},
                               {"role": "user", "content": prompt}],
                              temperature=0.1, max_tokens=400)
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return {"error": f"LLM 输出非 JSON: {raw[:100]}"}
        obj = json.loads(m.group(0))
        # 白名单校验
        patch = obj.get("patch") or {}
        cleaned = {}
        for k, v in patch.items():
            if k in PATCHABLE and re.match(PATCHABLE[k], str(v)):
                cleaned[k] = v
        obj["patch"] = cleaned
        obj["cause"] = obj.get("cause") or "unknown"
        # 证据不足/低置信 → 不给等待型补丁(这类建议等于没定位)
        if obj.get("cause") in ("unknown",) or obj.get("confidence") == "low":
            obj["patch"] = {}
            obj["action"] = obj.get("action") or "diagnose"
        obj["diagnosis"] = obj.get("explain") or obj.get("diagnosis") or ""
        obj["patch_reason"] = obj.get("evidence") or ""
        return obj
    except Exception as e:
        return {"error": f"LLM 调用失败: {str(e)[:150]}"}


def apply_patch_hint(ad, patch: dict):
    """把补丁应用到适配器(仅影响下一次重试)。nav_text 覆盖导航目标, wait_ms 覆盖节奏。"""
    if not patch:
        return
    if "nav_text" in patch:
        ad.nav_override = patch["nav_text"]
    if "wait_ms" in patch:
        try:
            ad.wait_override_ms = int(patch["wait_ms"])
        except ValueError:
            pass
