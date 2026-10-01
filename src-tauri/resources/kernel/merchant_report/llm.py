# -*- coding: utf-8 -*-
"""LLM 层: OpenAI 兼容 /v1/chat/completions, 流式; LLM 挂掉不影响核心流程(所有调用可失败)。"""
import json
import ssl
import urllib.request

try:
    import certifi

    def _ssl_ctx():
        return ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover
    def _ssl_ctx():
        return ssl.create_default_context()


class LLMClient:
    def __init__(self, base_url, api_key, model, timeout=120):
        self.base = (base_url or "").rstrip("/")
        self.key = api_key or ""
        self.model = model or ""
        self.timeout = timeout

    @property
    def configured(self):
        return bool(self.base and self.key and self.model)

    def chat(self, messages, on_delta=None, temperature=0.4, max_tokens=1200):
        """返回完整文本; on_delta(text) 流式回调。失败抛异常(调用方决定降级)。"""
        payload = {"model": self.model, "messages": messages, "stream": True,
                   "temperature": temperature, "max_tokens": max_tokens}
        req = urllib.request.Request(
            self.base + "/chat/completions", data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=_ssl_ctx()))
        resp = opener.open(req, timeout=self.timeout)
        chunks = []
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0]["delta"].get("content")
            except Exception:
                continue
            if delta:
                chunks.append(delta)
                if on_delta:
                    on_delta(delta)
        return "".join(chunks)

    def test_connection(self):
        """真调一次极简对话(设置页测试连接用), 返回 (ok, 文案)"""
        if not self.configured:
            return False, "未配置完整（Base URL / Key / 模型）"
        try:
            text = self.chat([{"role": "user", "content": "请只回复：连接成功"}], max_tokens=20)
            return True, f"连接成功：{text[:30]}"
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "API Key 无效（401）"
            if e.code == 404:
                return False, "路径或模型不存在（检查 Base URL 是否含 /v1、模型 ID 是否完整）"
            return False, f"HTTP {e.code}（模型 ID 或参数错误）"
        except Exception as e:
            return False, f"网络无法连接: {str(e)[:80]}"


DAILY_REPORT_PROMPT = """你是餐饮门店运营分析师。基于以下JSON数据写一份简短日报解读（300字内）：
1. 一句话总评（对照营业额与订单）
2. 本店 vs 商圈TOP10 的最大差距点（引用具体数字，不编造）
3. 3 条可执行建议（每条不超过25字，具体到动作）
数据: {data}
要求：口语化、直接、不套话；数字必须来自给定数据。"""


def daily_report(llm: LLMClient, data: dict, on_delta=None):
    """日报解读; 返回文本。LLM 不可用抛异常, 调用方降级。"""
    prompt = DAILY_REPORT_PROMPT.replace("{data}", json.dumps(data, ensure_ascii=False))
    return llm.chat([{"role": "system", "content": "你是精炼的门店运营顾问，输出中文。"},
                     {"role": "user", "content": prompt}], on_delta=on_delta, max_tokens=600)
