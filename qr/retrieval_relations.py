"""项目关系图谱 → 检索扩展（阶段 C）。"""
from __future__ import annotations

import re
from typing import Any

from . import config, db, project_relations, workspace

_CROSS_HINTS = (
    "协作", "配合", "一起", "跨项目", "关联", "依赖", "支撑", "怎么配合",
)
_CROSS_PAIR = re.compile(
    r"([a-zA-Z][\w-]{2,})\s*(?:和|与|跟)\s*([a-zA-Z][\w-]{2,})",
    re.I,
)


def _link_types(cfg: dict[str, Any]) -> tuple[str, ...]:
    raw = cfg.get("retrieval_relation_link_types")
    if isinstance(raw, list) and raw:
        return tuple(str(x) for x in raw if str(x) in project_relations.LINK_TYPES)
    return ("depends", "supports", "related", "co_dev")


def expand_projects(project_id: str, cfg: dict[str, Any] | None = None) -> list[str]:
    """沿 project_links 1 跳扩展关联 project_id（不含自身）。"""
    cfg = cfg or config.load_config()
    if not cfg.get("retrieval_relation_expand", True):
        return []
    pid = workspace.normalize_project_id(project_id)
    if not pid:
        return []
    max_n = max(1, int(cfg.get("retrieval_relation_max_projects", 2)))
    types = _link_types(cfg)
    placeholders = ",".join("?" * len(types))
    with db.session() as conn:
        project_relations.ensure_schema(conn)
        rows = conn.execute(
            f"SELECT from_project, to_project, strength FROM project_links "
            f"WHERE link_type IN ({placeholders}) "
            f"AND (from_project=? OR to_project=?) "
            f"ORDER BY strength DESC",
            (*types, pid, pid),
        ).fetchall()
    seen = {pid.lower()}
    out: list[str] = []
    for r in rows:
        fp, tp = r["from_project"], r["to_project"]
        other = tp if fp == pid else fp
        key = other.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(other)
        if len(out) >= max_n:
            break
    return out


def _match_project_name(token: str) -> str | None:
    token = token.strip().lower().replace("_", "-")
    if not token or len(token) < 3:
        return None
    grouped = workspace.list_projects_grouped(300)
    for pid in grouped.get("projects") or []:
        cat, name = workspace.parse_project_id(pid)
        aliases = {pid.lower(), name.lower(), token}
        if token in aliases or token in name.lower() or name.lower() in token:
            return pid
        if token.replace("-", "") in name.lower().replace("-", ""):
            return pid
    return None


def resolve_cross_project(
    question: str,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """口语跨项目问法 → primary + related 列表。"""
    cfg = cfg or config.load_config()
    if not cfg.get("retrieval_relation_expand", True):
        return None
    q = question.strip()
    if not q:
        return None
    if not any(h in q for h in _CROSS_HINTS) and not _CROSS_PAIR.search(q):
        return None
    m = _CROSS_PAIR.search(q)
    if m:
        a = _match_project_name(m.group(1))
        b = _match_project_name(m.group(2))
        if a and b and a != b:
            return {"primary": a, "related": [b], "mode": "pair"}
    refs: list[str] = []
    for m in re.finditer(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b", q, re.I):
        pid = _match_project_name(m.group(1))
        if pid and pid not in refs:
            refs.append(pid)
    if len(refs) >= 2:
        return {"primary": refs[0], "related": refs[1:], "mode": "multi"}
    return None


def relation_discount(cfg: dict[str, Any] | None = None) -> float:
    cfg = cfg or config.load_config()
    return float(cfg.get("retrieval_relation_discount", 0.85))


def tag_related_hit(h: dict, *, anchor: str, related: str, discount: float) -> dict:
    nh = dict(h)
    nh["relation_expanded"] = True
    nh["relation_anchor"] = anchor
    nh["relation_project"] = related
    sc = float(nh.get("score") or 0.0)
    nh["score"] = sc * discount
    scores = dict(nh.get("scores") or {})
    if scores:
        scores["final"] = round(float(scores.get("final", sc)) * discount, 4)
        scores["relation_discount"] = discount
        nh["scores"] = scores
    return nh


def context_block(hits: list[dict]) -> str:
    """为问答 prompt 生成关联项目说明。"""
    rel = [h for h in hits if h.get("relation_expanded")]
    if not rel:
        return ""
    by_proj: dict[str, list[str]] = {}
    for h in rel[:6]:
        rp = str(h.get("relation_project") or "")
        if not rp:
            continue
        by_proj.setdefault(rp, []).append(h.get("path") or "")
    if not by_proj:
        return ""
    lines = ["【关联项目检索扩展】（沿 project_links 1 跳，分数已降权）"]
    for pid, paths in by_proj.items():
        sample = ", ".join(dict.fromkeys(paths[:2]))
        lines.append(f"- {pid}: {sample}")
    return "\n".join(lines)
