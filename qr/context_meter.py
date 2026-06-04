from __future__ import annotations

from . import config, models
from .query import SYSTEM

HISTORY_LIMIT = 20
_SUFFIX_TOKENS = 120


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return max(1, int(cjk / 1.5 + other / 4))


def format_history(history: list[dict]) -> str:
    lines = []
    for m in history:
        role = "用户" if m.get("role") == "user" else "助手"
        lines.append(f"{role}: {m.get('content', '')}")
    return "【对话历史】\n" + "\n\n".join(lines)


def _format_hits(hits: list[dict]) -> str:
    return "【本地上下文】\n" + "\n\n".join(
        f"[来源 {i + 1}] {h.get('path', '')}"
        + (f" · {h['project']}" if h.get("project") else "")
        + f"\n{h.get('text', '')}"
        for i, h in enumerate(hits)
    )


def _format_web(web_results: list[dict]) -> str:
    return "【网络搜索结果】\n" + "\n\n".join(
        f"[网络 {i + 1}] {w.get('title', '')} ({w.get('url', '')})\n{w.get('snippet', '')}"
        for i, w in enumerate(web_results)
    )


def _context_limit(*, deep: bool = False, model: str | None = None) -> int:
    cfg = config.load_config()
    reasoning = models.is_reasoning_model(model, cfg) if model else deep
    key = "deep_context_tokens" if reasoning else "context_tokens"
    return int(cfg.get(key, 32768))


def _retrieval_tokens(k: int, hits: list[dict] | None, cfg: dict) -> int:
    if hits:
        return estimate_tokens(_format_hits(hits))
    chunk = int(cfg.get("chunk_chars", 1200))
    per = estimate_tokens(" " * chunk) + estimate_tokens("/path/to/file.ext")
    return k * per


def _web_tokens(web: bool, web_results: list[dict] | None, cfg: dict) -> int:
    if not web:
        return 0
    if web_results:
        return estimate_tokens(_format_web(web_results))
    n = int(cfg.get("web_results", 5))
    return n * estimate_tokens("title url\nsnippet " * 20)


def estimate_ask_context(
    *,
    history: list[dict] | None = None,
    question: str = "",
    k: int = 6,
    web: bool = False,
    deep: bool = False,
    model: str | None = None,
    hits: list[dict] | None = None,
    web_results: list[dict] | None = None,
) -> dict:
    cfg = config.load_config()
    limit = _context_limit(deep=deep, model=model)
    history = history or []

    system_t = estimate_tokens(SYSTEM)
    history_t = estimate_tokens(format_history(history)) if history else 0
    retrieval_t = _retrieval_tokens(k, hits, cfg)
    web_t = _web_tokens(web, web_results, cfg)
    question_t = estimate_tokens(question)
    suffix_t = _SUFFIX_TOKENS

    used = system_t + history_t + retrieval_t + web_t + question_t + suffix_t
    used = min(used, limit)
    remaining = max(0, limit - used)
    used_pct = round(used / limit * 100, 1) if limit else 0.0
    remaining_pct = round(remaining / limit * 100, 1) if limit else 0.0

    return {
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "pct": used_pct,
        "remaining_pct": remaining_pct,
        "history_messages": len(history),
        "history_limit": HISTORY_LIMIT,
        "breakdown": {
            "system": system_t,
            "history": history_t,
            "retrieval": retrieval_t,
            "web": web_t,
            "question": question_t,
            "overhead": suffix_t,
        },
    }
