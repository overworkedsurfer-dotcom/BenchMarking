"""Minimal OpenAI-compatible Chat Completions client (stdlib only).

Works with OpenAI, Azure-style gateways, OpenRouter, Together, Groq, Fireworks, DeepSeek, vLLM, SGLang,
llama.cpp server, Ollama (``/v1``), LM Studio, LiteLLM proxies — anything exposing ``POST /chat/completions``.
"""

from __future__ import annotations

import json
import os
import random
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class LLMError(Exception):
    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class ModelConfig:
    """How to reach one model. ``name`` is the label used in results and leaderboards."""

    name: str
    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    params: dict = field(default_factory=dict)  # extra body params: temperature, max_tokens, reasoning_effort...
    headers: dict = field(default_factory=dict)
    tool_mode: str = "native"  # native (function calling) or text (JSON action blocks)
    timeout_s: float = 600.0
    max_retries: int = 6

    def public_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("api_key", "headers")}
        d["headers"] = sorted(self.headers)
        return d


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if bundle and os.path.exists(bundle):
        ctx.load_verify_locations(bundle)
    return ctx


class ChatClient:
    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg
        self.url = cfg.base_url.rstrip("/") + "/chat/completions"
        self._ctx = _ssl_context() if self.url.startswith("https") else None

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        body = {"model": self.cfg.model, "messages": messages, **self.cfg.params}
        if tools:
            body["tools"] = tools
        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", **self.cfg.headers}
        if self.cfg.api_key:
            headers.setdefault("Authorization", f"Bearer {self.cfg.api_key}")
        last: Exception | None = None
        for attempt in range(self.cfg.max_retries + 1):
            req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout_s, context=self._ctx) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                if "error" in payload and not payload.get("choices"):
                    raise LLMError(f"API error: {payload['error']}", retryable=True)
                if not payload.get("choices"):
                    raise LLMError(f"response has no choices: {str(payload)[:300]}", retryable=True)
                return payload
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")[:800]
                retryable = e.code in (408, 409, 425, 429) or e.code >= 500
                last = LLMError(f"HTTP {e.code}: {text}", status=e.code, retryable=retryable)
                if not retryable:
                    raise last from e
                retry_after = e.headers.get("Retry-After") if e.headers else None
                delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else None
            except LLMError as e:
                last = e
                delay = None
                if not e.retryable:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError, OSError) as e:
                last = LLMError(f"{type(e).__name__}: {e}", retryable=True)
                delay = None
            if attempt < self.cfg.max_retries:
                time.sleep(delay if delay is not None else min(60.0, 2 ** attempt + random.random()))
        raise last or LLMError("request failed")
