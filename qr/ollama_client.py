from __future__ import annotations

import json
import re

import httpx

from . import config

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaError(RuntimeError):
    pass


class Ollama:
    def __init__(self, url: str | None = None, embed_model: str | None = None,
                 chat_model: str | None = None) -> None:
        cfg = config.load_config()
        self.url = (url or cfg["ollama_url"]).rstrip("/")
        self.embed_model = embed_model or cfg["embed_model"]
        self.chat_model = chat_model or cfg["chat_model"]
        # trust_env=False：忽略系统/环境代理，本机直连 ollama（避免被 Clash 等代理拦截返回 502）
        self._client = httpx.Client(trust_env=False, timeout=600.0)

    def embed(self, text: str) -> list[float]:
        try:
            r = self._client.post(
                f"{self.url}/api/embeddings",
                json={"model": self.embed_model, "prompt": text},
                timeout=120.0,
            )
            r.raise_for_status()
            emb = r.json().get("embedding")
        except httpx.HTTPError as e:
            raise OllamaError(f"embedding 调用失败: {e}") from e
        if not emb:
            raise OllamaError("embedding 返回为空，确认 ollama 正在运行且已拉取嵌入模型")
        return emb

    def generate(self, prompt: str, system: str | None = None,
                 model: str | None = None, strip_think: bool = True,
                 num_ctx: int | None = None) -> str:
        cfg = config.load_config()
        resolved = model or self.chat_model
        if num_ctx is None:
            if resolved == cfg.get("deep_model"):
                num_ctx = int(cfg.get("deep_context_tokens", 131072))
            else:
                num_ctx = int(cfg.get("context_tokens", 32768))
        payload: dict = {
            "model": resolved,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_ctx": num_ctx},
        }
        if system:
            payload["system"] = system
        try:
            r = self._client.post(f"{self.url}/api/generate", json=payload, timeout=600.0)
            r.raise_for_status()
            text = r.json().get("response", "")
        except httpx.HTTPError as e:
            raise OllamaError(f"生成调用失败: {e}") from e
        if strip_think:
            text = _THINK_RE.sub("", text).strip()
        return text

    def generate_stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        strip_think: bool = True,
        num_ctx: int | None = None,
    ):
        cfg = config.load_config()
        resolved = model or self.chat_model
        if num_ctx is None:
            if resolved == cfg.get("deep_model"):
                num_ctx = int(cfg.get("deep_context_tokens", 131072))
            else:
                num_ctx = int(cfg.get("context_tokens", 32768))
        payload: dict = {
            "model": resolved,
            "prompt": prompt,
            "stream": True,
            "options": {"temperature": 0.3, "num_ctx": num_ctx},
        }
        if system:
            payload["system"] = system
        think_open = "<think>"
        think_close = "</think>"
        in_think = False
        pending = ""
        try:
            with self._client.stream(
                "POST", f"{self.url}/api/generate", json=payload, timeout=600.0,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    pending += data.get("response", "")
                    if not strip_think:
                        if pending:
                            yield pending
                            pending = ""
                    else:
                        while pending:
                            if in_think:
                                end = pending.find(think_close)
                                if end == -1:
                                    pending = ""
                                    break
                                pending = pending[end + len(think_close):]
                                in_think = False
                            else:
                                start = pending.find(think_open)
                                if start == -1:
                                    yield pending
                                    pending = ""
                                    break
                                if start > 0:
                                    yield pending[:start]
                                pending = pending[start + len(think_open):]
                                in_think = True
                    if data.get("done"):
                        if pending and not strip_think:
                            yield pending
                        elif pending and strip_think and not in_think:
                            yield pending
                        break
        except httpx.HTTPError as e:
            raise OllamaError(f"流式生成失败: {e}") from e

    def health(self) -> list[str]:
        try:
            r = self._client.get(f"{self.url}/api/tags", timeout=10.0)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        except httpx.HTTPError as e:
            raise OllamaError(f"无法连接 ollama ({self.url}): {e}") from e
