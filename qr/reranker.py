"""检索重排：词项重叠 + 可选配置权重。"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]{2,}")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def lexical_boost(question: str, hit_text: str) -> float:
    q = _tokens(question)
    if not q:
        return 0.0
    h = _tokens(hit_text)
    if not h:
        return 0.0
    overlap = len(q & h) / max(len(q), 1)
    return min(0.25, overlap * 0.25)


def rerank_hits(question: str, hits: list[dict], k: int, *, enabled: bool = True) -> list[dict]:
    if not enabled or not hits:
        return hits[:k]
    out: list[dict] = []
    for h in hits:
        boost = lexical_boost(question, h.get("text", ""))
        base = float(h.get("score", 0.0))
        nh = dict(h)
        nh["score"] = base + boost
        scores = dict(nh.get("scores") or {})
        scores["lexical"] = round(boost, 4)
        scores["final"] = round(nh["score"], 4)
        nh["scores"] = scores
        out.append(nh)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:k]
