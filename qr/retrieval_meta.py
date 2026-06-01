from __future__ import annotations

from pathlib import Path


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


def project_name_from_path(path: str) -> str | None:
    p = Path(path.replace("\\", "/"))
    parts = [x for x in p.parts if x]
    if not parts:
        return None
    if "QR" in parts:
        i = parts.index("QR")
        if i + 2 < len(parts):
            return f"{parts[i + 1]}/{parts[i + 2]}"
        if i + 1 < len(parts):
            return parts[i + 1]
    if "Projects" in parts:
        i = parts.index("Projects")
        if i + 1 < len(parts):
            return f"legacy/{parts[i + 1]}"
    if parts[0].startswith("cursor-"):
        return parts[0].replace("cursor-", "", 1)
    return p.stem if p.suffix else parts[-1]
