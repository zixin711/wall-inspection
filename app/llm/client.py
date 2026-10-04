"""OpenAI 兼容多模态客户端。

私有化部署下后端通常是 vLLM / SGLang / Ollama / Xinference。这些实现对
response_format 的支持参差不齐，因此：
  · LLM_JSON_MODE=true  时优先用 response_format
  · 服务端报不支持就自动退回文本模式，并从回复里抽取 JSON
  · 解析失败重试一次并把错误回喂给模型；再失败则返回 None，由上层降级为纯 CV 结果
坏 JSON 永远不会抛到前端。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

log = logging.getLogger("mould.llm")

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


class LLMUnavailable(Exception):
    pass


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK.search(t)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class VisionClient:
    def __init__(self, settings) -> None:
        self.s = settings
        self._json_mode = bool(settings.llm_json_mode)

    @property
    def _url(self) -> str:
        return self.s.llm_base_url.rstrip("/") + "/chat/completions"

    def _payload(self, messages: List[Dict[str, Any]], json_mode: bool) -> Dict[str, Any]:
        p: Dict[str, Any] = {
            "model": self.s.llm_model,
            "messages": messages,
            "temperature": self.s.llm_temperature,
            "max_tokens": self.s.llm_max_tokens,
            "stream": False,
        }
        if json_mode:
            p["response_format"] = {"type": "json_object"}
        return p

    async def complete(self, messages: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """返回 (解析后的 JSON 或 None, 审计信息)。"""
        audit: Dict[str, Any] = {"model": self.s.llm_model, "attempts": 0, "json_mode": self._json_mode}
        headers = {"Authorization": f"Bearer {self.s.llm_api_key}", "Content-Type": "application/json"}
        convo = list(messages)
        json_mode = self._json_mode

        async with httpx.AsyncClient(timeout=self.s.llm_timeout_s) as client:
            for attempt in range(self.s.llm_max_retries + 1):
                audit["attempts"] = attempt + 1
                try:
                    resp = await client.post(self._url, headers=headers,
                                             json=self._payload(convo, json_mode))
                except httpx.RequestError as exc:
                    log.warning("大模型连接失败 (%s/%s): %s", attempt + 1, self.s.llm_max_retries + 1, exc)
                    audit["error"] = f"connect: {exc}"
                    continue

                if resp.status_code >= 400:
                    body = resp.text[:400]
                    audit["error"] = f"http {resp.status_code}: {body}"
                    # 推理框架不认 response_format 时降级重来，不算掉一次重试
                    if json_mode and resp.status_code in (400, 422) and "response_format" in body:
                        log.info("服务端不支持 response_format，退回文本 JSON 模式")
                        json_mode = False
                        self._json_mode = False
                        audit["json_mode"] = False
                        continue
                    log.warning("大模型返回错误: %s", audit["error"])
                    continue

                try:
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"]
                    audit["response_id"] = data.get("id")
                    audit["usage"] = data.get("usage")
                except (ValueError, KeyError, IndexError) as exc:
                    audit["error"] = f"malformed response: {exc}"
                    continue

                parsed = extract_json(text if isinstance(text, str) else _join_content(text))
                if parsed is not None:
                    audit.pop("error", None)
                    return parsed, audit

                audit["error"] = "unparseable json"
                if attempt < self.s.llm_max_retries:
                    convo = list(messages) + [
                        {"role": "assistant", "content": str(text)[:2000]},
                        {"role": "user", "content":
                            "你的上一条回复不是合法 JSON。请只输出一个 JSON 对象，"
                            "不要 Markdown 代码块，不要任何其他文字。"},
                    ]

        return None, audit


def _join_content(content: Any) -> str:
    """有些实现把 content 返回成分段数组。"""
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content)
