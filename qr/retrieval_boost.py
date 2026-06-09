"""检索加分规则（可配置）与按路径去重。"""
from __future__ import annotations

from typing import Any, Callable

from . import config, retrieval_meta

# 规则字段说明：
# - boost: 加分值
# - qr_query: True 时仅当问题判定为 QR 自指
# - question_any: 问题含任一子串（不区分大小写）
# - path_any: 路径含任一子串
# - path_suffix: 路径以任一后缀结尾
# - path_one: 多条路径命中只加一次（如核心模块文件）
DEFAULT_BOOST_RULES: list[dict[str, Any]] = [
    {"boost": 0.18, "qr_query": True, "path_any": ["/qr/dev/qr/", "/projects/qr/"]},
    {
        "boost": 0.16,
        "qr_query": True,
        "path_any": ["/.qr/", "qr-config"],
        "path_suffix": ["config.json"],
    },
    {
        "boost": 0.08,
        "qr_query": True,
        "path_one": [
            "/qr/config.py", "/qr/qr/config.py",
            "/qr/cli.py", "/qr/qr/cli.py",
            "/qr/chat.py", "/qr/qr/chat.py",
            "/qr/web.py", "/qr/qr/web.py",
            "/qr/db.py", "/qr/qr/db.py",
        ],
    },
    {
        "boost": 0.38,
        "question_any": ["context_tokens", "deep_context_tokens"],
        "path_any": ["/.qr/config.json", "qr-config"],
    },
    {
        "boost": 0.14,
        "question_any": ["context_tokens", "deep_context_tokens"],
        "path_any": ["/dev/qr/qr/config.py"],
        "path_suffix": ["/qr/config.py"],
    },
    {
        "boost": 0.12,
        "question_any": ["context_tokens", "deep_context_tokens"],
        "path_any": ["ollama_client.py", "indexer.py"],
    },
    {
        "boost": 0.32,
        "question_any": ["端口", "port", "3000", "8765"],
        "path_any": ["/.qr/config.json", "qr-config"],
    },
    {
        "boost": 0.18,
        "question_any": ["端口", "port", "3000", "8765"],
        "path_all": ["indexer.py", "/dev/qr/"],
    },
    {
        "boost": 0.10,
        "question_any": ["端口", "port", "3000", "8765"],
        "path_any": ["cli.py", "web.py"],
    },
    {"boost": 0.22, "dynamic": "project_ref"},
    {"boost": 0.05, "path_any": ["/agent-transcripts/", "cursor-"]},
]

DEFAULT_SOURCE_ADJUST: list[dict[str, Any]] = [
    {"qr_query": True, "source_type": "transcript", "boost": -0.06},
    {"qr_query": True, "source_type_in": ["config", "manifest", "code"], "boost": 0.04},
    {
        "question_any": ["前端框架", "package.json"],
        "source_type": "manifest",
        "boost": 0.08,
    },
    {
        "question_any": ["前端框架", "package.json"],
        "source_type": "transcript",
        "boost": -0.08,
    },
]


def _path_matches(path: str, rule: dict[str, Any]) -> bool:
    p = path.lower().replace("\\", "/")
    if rule.get("path_one"):
        return any(pat in p for pat in rule["path_one"])
    if rule.get("path_all"):
        return all(pat in p for pat in rule["path_all"])
    checks: list[bool] = []
    if rule.get("path_any"):
        checks.append(any(pat in p for pat in rule["path_any"]))
    if rule.get("path_suffix"):
        checks.append(any(p.endswith(suf) for suf in rule["path_suffix"]))
    if not checks:
        return True
    return any(checks)


def _question_matches(question: str, ql: str, rule: dict[str, Any]) -> bool:
    if rule.get("question_any"):
        return any(s.lower() in ql for s in rule["question_any"])
    return True


def path_boost(
    path: str,
    question: str,
    *,
    is_qr_query: Callable[[str], bool],
    project_ref_boost: Callable[[str, str], float],
    cfg: dict[str, Any] | None = None,
) -> float:
    cfg = cfg or config.load_config()
    rules = cfg.get("retrieval_boost_rules") or DEFAULT_BOOST_RULES
    ql = question.lower()
    is_qr = is_qr_query(question)
    total = 0.0
    for rule in rules:
        if rule.get("qr_query") and not is_qr:
            continue
        if rule.get("dynamic") == "project_ref":
            total += project_ref_boost(path, question)
            continue
        if not _question_matches(question, ql, rule):
            continue
        if not _path_matches(path, rule):
            continue
        total += float(rule.get("boost", 0.0))
    return total


def source_type_adjust(
    path: str,
    question: str,
    *,
    is_qr_query: Callable[[str], bool],
    cfg: dict[str, Any] | None = None,
) -> float:
    cfg = cfg or config.load_config()
    rules = cfg.get("retrieval_source_adjust") or DEFAULT_SOURCE_ADJUST
    ql = question.lower()
    st = retrieval_meta.classify_source_type(path)
    adj = 0.0
    for rule in rules:
        if rule.get("qr_query") and not is_qr_query(question):
            continue
        if rule.get("question_any") and not any(s.lower() in ql for s in rule["question_any"]):
            continue
        if rule.get("source_type") and st != rule["source_type"]:
            continue
        if rule.get("source_type_in") and st not in rule["source_type_in"]:
            continue
        adj += float(rule.get("boost", 0.0))
    return adj


def dedupe_by_path(hits: list[dict], k: int, *, max_per_path: int = 2) -> list[dict]:
    """同文件最多保留 max_per_path 条，凑满 k 条为止。"""
    if max_per_path < 1:
        max_per_path = 1
    counts: dict[str, int] = {}
    out: list[dict] = []
    for h in hits:
        path = h.get("path") or ""
        if counts.get(path, 0) >= max_per_path:
            continue
        counts[path] = counts.get(path, 0) + 1
        out.append(h)
        if len(out) >= k:
            break
    return out


def vec_fetch_limit(k: int, project: str | None, category: str | None, cfg: dict | None = None) -> int:
    cfg = cfg or config.load_config()
    if project or category:
        mult = max(2, int(cfg.get("retrieval_vec_oversample", 8)))
        return k * mult
    return k
