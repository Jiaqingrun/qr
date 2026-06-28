from __future__ import annotations

import json
import re
import time

import httpx

from . import config, models

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_NAN_EMBED_MARKERS = ("NaN", "unsupported value", "invalid embedding")


def _is_retriable_embed_error(err: str) -> bool:
    low = err.lower()
    return any(m.lower() in low for m in _NAN_EMBED_MARKERS) or "500" in err


def _embed_error_message(err: str) -> str:
    if _is_retriable_embed_error(err):
        return (
            f"embedding 调用失败: {err}。"
            "常见于 Ollama 嵌入模型在 Flash Attention 下的数值溢出；"
            "请重启 Ollama 并设置 OLLAMA_FLASH_ATTENTION=false（Homebrew: "
            "launchctl setenv OLLAMA_FLASH_ATTENTION false && brew services restart ollama）。"
        )
    return f"embedding 调用失败: {err}"


def _parse_embed_json(data: dict) -> list[float] | None:
    embs = data.get("embeddings")
    if embs:
        return embs[0]
    emb = data.get("embedding")
    if emb:
        return emb
    return None


def _embed_text_variants(text: str) -> list[str]:
    """为易触发 NaN 的输入准备降级文本。"""
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)

    add(text)
    collapsed = " ".join(text.split())
    add(collapsed)
    for n in (3000, 2000, 1200, 600, 200):
        if len(text) > n:
            add(text[:n])
    return out


class OllamaError(RuntimeError):
    pass


class Ollama:
    def __init__(self, url: str | None = None, embed_model: str | None = None,
                 chat_model: str | None = None) -> None:
        cfg = config.load_config()
        self.url = (url or cfg["ollama_url"]).rstrip("/")
        self.embed_model = embed_model or cfg["embed_model"]
        self.chat_model = chat_model or cfg["chat_model"]
        self.embed_dim = int(cfg.get("embed_dim") or 0)
        # trust_env=False：忽略系统/环境代理，本机直连 ollama（避免被 Clash 等代理拦截返回 502）
        self._client = httpx.Client(trust_env=False, timeout=600.0)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client.close()

    def __enter__(self) -> Ollama:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def embed(self, text: str) -> list[float]:
        best_err: OllamaError | None = None
        for variant in _embed_text_variants(text):
            try:
                emb = self._embed_via_api(variant)
            except OllamaError as e:
                best_err = e
                continue
            if not emb:
                continue
            if self.embed_dim and len(emb) != self.embed_dim:
                raise OllamaError(
                    f"embedding 维度 {len(emb)} 与 config embed_dim {self.embed_dim} 不一致"
                )
            return emb
        if best_err:
            raise best_err
        raise OllamaError("embedding 返回为空，确认 ollama 正在运行且已拉取嵌入模型")

    def _embed_variants_for(self, text: str) -> list[tuple[str, dict]]:
        variants: list[tuple[str, dict]] = []
        if self.embed_dim:
            variants.append((
                "/api/embed",
                {
                    "model": self.embed_model,
                    "input": text,
                    "dimensions": self.embed_dim,
                },
            ))
        variants.append(("/api/embed", {"model": self.embed_model, "input": text}))
        for prefix in ("passage: ", "text: "):
            variants.append((
                "/api/embed",
                {"model": self.embed_model, "input": f"{prefix}{text}"},
            ))
        return variants

    def _embed_via_api(self, text: str) -> list[float] | None:
        variants = self._embed_variants_for(text)

        last_err: OllamaError | None = None
        for i, (endpoint, payload) in enumerate(variants):
            try:
                return self._embed_request(endpoint, payload)
            except OllamaError as e:
                last_err = e
                if not _is_retriable_embed_error(str(e)):
                    raise
                if i < len(variants) - 1:
                    time.sleep(0.25)
        if last_err:
            raise last_err
        return None

    def _embed_request(self, endpoint: str, payload: dict) -> list[float]:
        try:
            r = self._client.post(
                f"{self.url}{endpoint}", json=payload, timeout=120.0,
            )
        except httpx.HTTPError as e:
            raise OllamaError(_embed_error_message(str(e))) from e
        if r.status_code >= 400:
            try:
                body = r.json()
                err = body.get("error") or r.text
            except json.JSONDecodeError:
                err = r.text or f"HTTP {r.status_code}"
            raise OllamaError(_embed_error_message(str(err)))
        data = r.json()
        emb = _parse_embed_json(data)
        if not emb:
            raise OllamaError("embedding 返回为空")
        return emb

    def probe_embed(self, text: str = "qr-health-probe") -> None:
        """验证嵌入 API（不仅 /api/tags）。"""
        self.embed(text)

    def generate(self, prompt: str, system: str | None = None,
                 model: str | None = None, strip_think: bool = True,
                 num_ctx: int | None = None, timeout: float | None = None) -> str:
        cfg = config.load_config()
        resolved = model or self.chat_model
        if num_ctx is None:
            if models.is_reasoning_model(resolved, cfg):
                num_ctx = int(cfg.get("deep_context_tokens", 131072))
            else:
                num_ctx = int(cfg.get("context_tokens", 32768))
        strip_think = strip_think and models.is_reasoning_model(resolved, cfg)
        payload: dict = {
            "model": resolved,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_ctx": num_ctx},
        }
        if system:
            payload["system"] = system
        req_timeout = 600.0 if timeout is None else float(timeout)
        try:
            r = self._client.post(
                f"{self.url}/api/generate", json=payload, timeout=req_timeout,
            )
            r.raise_for_status()
            text = r.json().get("response", "")
        except httpx.HTTPError as e:
            if isinstance(e, httpx.TimeoutException):
                raise OllamaError(
                    f"生成超时（已等待 {int(req_timeout)} 秒）。"
                    "规范修订 prompt 较长，请稍候重试；"
                    "或在 ~/.qr/config.json 提高 standards_revise_timeout_seconds。"
                ) from e
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
            if models.is_reasoning_model(resolved, cfg):
                num_ctx = int(cfg.get("deep_context_tokens", 131072))
            else:
                num_ctx = int(cfg.get("context_tokens", 32768))
        strip_think = strip_think and models.is_reasoning_model(resolved, cfg)
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
