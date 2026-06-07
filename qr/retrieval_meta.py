from __future__ import annotations


def classify_source_type(path: str) -> str:
    p = (path or "").lower().replace("\\", "/")
    if "/agent-transcripts/" in p or p.endswith(".jsonl"):
        return "transcript"
    if "/.qr/" in p or p.endswith("config.json"):
        return "config"
    if any(x in p for x in ("package.json", "pyproject.toml", "next.config", "vite.config")):
        return "manifest"
    if any(p.endswith(ext) for ext in (".py", ".js", ".ts", ".tsx", ".jsx", ".vue", ".go", ".rs")):
        return "code"
    if any(p.endswith(ext) for ext in (".md", ".txt", ".rst")):
        return "doc"
    if "summaries" in p:
        return "summary"
    return "other"


def annotate_hit(h: dict, question: str, path_boost: float) -> dict:
    vec = float(h.get("vec_score") or 0.0)
    fts = float(h.get("fts_score") or 0.0)
    rrf = float(h.get("rrf") or h.get("score") or 0.0)
    boost = float(path_boost)
    final = rrf + boost
    return {
        **h,
        "source_type": classify_source_type(h.get("path", "")),
        "scores": {
            "vector": round(vec, 4),
            "fts": round(fts, 4),
            "rrf": round(rrf, 4),
            "path_boost": round(boost, 4),
            "final": round(final, 4),
        },
        "score": final,
    }
