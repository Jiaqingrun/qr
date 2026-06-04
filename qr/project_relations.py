"""项目—项目关系：组合（Suite）、协作边、自动推断与手动维护。"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from . import config, db, facts, project_brief, workspace

LINK_TYPES = ("depends", "supports", "related", "co_dev")
LINK_TYPE_LABELS = {
    "depends": "依赖",
    "supports": "支撑",
    "related": "关联",
    "co_dev": "共建",
}
ROLE_LABELS = {
    "infrastructure": "基础设施",
    "product": "业务产品",
    "experiment": "实验探索",
    "tooling": "工具脚本",
    "legacy": "遗留/归档",
}

_DEFAULT_SUITE_COLORS = ("#7ee0a8", "#6ec8ff", "#c4a8ff", "#ffb86c", "#ff79c6", "#8be9fd")

_README_DEP = re.compile(
    r"(?:依赖|depends?\s+on|requires?|使用|基于|built\s+on)[:：\s]+[`\"]?([a-zA-Z0-9_./-]+)[`\"]?",
    re.I,
)
_PROJECT_ID = re.compile(r"\b([a-z][\w-]*/[\w.-]+)\b", re.I)


def ensure_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS project_suites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            slug        TEXT NOT NULL UNIQUE,
            name        TEXT NOT NULL,
            description TEXT,
            role        TEXT,
            color       TEXT,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS project_suite_members (
            suite_id    INTEGER NOT NULL,
            project_id  TEXT NOT NULL,
            role_in_suite TEXT,
            note        TEXT,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (suite_id, project_id),
            FOREIGN KEY(suite_id) REFERENCES project_suites(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_suite_members_proj ON project_suite_members(project_id);

        CREATE TABLE IF NOT EXISTS project_links (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            from_project TEXT NOT NULL,
            to_project   TEXT NOT NULL,
            link_type    TEXT NOT NULL DEFAULT 'related',
            strength     INTEGER NOT NULL DEFAULT 50,
            reason       TEXT,
            evidence     TEXT,
            source       TEXT NOT NULL DEFAULT 'manual',
            pinned       INTEGER NOT NULL DEFAULT 0,
            created_at   INTEGER NOT NULL,
            updated_at   INTEGER NOT NULL,
            UNIQUE(from_project, to_project, link_type)
        );
        CREATE INDEX IF NOT EXISTS idx_project_links_from ON project_links(from_project);
        CREATE INDEX IF NOT EXISTS idx_project_links_to ON project_links(to_project);

        CREATE TABLE IF NOT EXISTS project_meta (
            project_id  TEXT PRIMARY KEY,
            role        TEXT,
            note        TEXT,
            updated_at  INTEGER NOT NULL
        );
        """
    )


def _now() -> int:
    return db.now()


def _slugify(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", name.strip().lower())
    return (s[:48].strip("-") or "suite")


def _all_project_ids(limit: int = 300) -> list[str]:
    data = workspace.list_projects_grouped(limit)
    return list(data.get("projects") or [])


def _project_aliases(pid: str) -> set[str]:
    cat, name = workspace.parse_project_id(pid)
    aliases = {pid.lower(), name.lower()}
    if cat:
        aliases.add(f"{cat}/{name}".lower())
        aliases.add(name.lower())
    d = workspace.resolve_project_dir(pid)
    if d:
        aliases.add(d.name.lower())
        aliases.add(str(d).lower())
    return {a for a in aliases if a}


def _match_project_token(token: str, catalog: dict[str, str]) -> str | None:
    t = token.strip().lower().replace("\\", "/")
    if not t:
        return None
    if t in catalog:
        return catalog[t]
    for alias, pid in catalog.items():
        if t == alias or t.endswith("/" + alias.split("/")[-1]):
            return pid
    return None


def _build_alias_catalog(pids: list[str]) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for pid in pids:
        for a in _project_aliases(pid):
            catalog[a] = pid
        cat, name = workspace.parse_project_id(pid)
        if cat:
            catalog[f"{cat}/{name}".lower()] = pid
    return catalog


def _read_readme(pid: str) -> str:
    d = workspace.resolve_project_dir(pid)
    if not d:
        return ""
    p = d / "README.md"
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _infer_from_readme(pids: list[str], catalog: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pid in pids:
        text = _read_readme(pid)
        if not text:
            continue
        for m in _README_DEP.finditer(text):
            target = _match_project_token(m.group(1), catalog)
            if target and target != pid:
                out.append(
                    {
                        "from_project": pid,
                        "to_project": target,
                        "link_type": "depends",
                        "strength": 72,
                        "reason": "README 声明依赖",
                        "evidence": [f"README: {m.group(0)[:120]}"],
                        "source": "inferred",
                    }
                )
        for m in _PROJECT_ID.finditer(text):
            target = _match_project_token(m.group(1), catalog)
            if target and target != pid:
                out.append(
                    {
                        "from_project": pid,
                        "to_project": target,
                        "link_type": "related",
                        "strength": 45,
                        "reason": "README 提及其它项目",
                        "evidence": [f"提及 {m.group(1)}"],
                        "source": "inferred",
                    }
                )
    return out


def _infer_event_cooccurrence(pids: list[str], days: int = 30) -> list[dict[str, Any]]:
    since = _now() - days * 86400
    pid_set = set(pids)
    buckets: dict[int, set[str]] = {}
    with db.session() as conn:
        rows = conn.execute(
            "SELECT ts, project FROM events WHERE ts>=? AND project IS NOT NULL",
            (since,),
        ).fetchall()
    for r in rows:
        p = str(r["project"] or "").strip()
        if not p or p not in pid_set or p.startswith("cursor-"):
            continue
        bucket = int(r["ts"]) // 3600
        buckets.setdefault(bucket, set()).add(p)
    pair_count: dict[tuple[str, str], int] = {}
    for projs in buckets.values():
        pl = sorted(projs)
        for i, a in enumerate(pl):
            for b in pl[i + 1 :]:
                key = (a, b)
                pair_count[key] = pair_count.get(key, 0) + 1
    out: list[dict[str, Any]] = []
    for (a, b), c in sorted(pair_count.items(), key=lambda x: -x[1]):
        if c < 2:
            continue
        strength = min(90, 35 + c * 8)
        out.append(
            {
                "from_project": a,
                "to_project": b,
                "link_type": "co_dev",
                "strength": strength,
                "reason": f"近 {days} 天行为同期活跃（{c} 小时窗口共现）",
                "evidence": [f"events 共现 ×{c}"],
                "source": "inferred",
            }
        )
    return out


def _infer_facts_cross(pids: list[str], catalog: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pid_set = set(pids)
    for f in facts.list_facts():
        proj = str(f.get("project") or "").strip()
        if not proj or proj not in pid_set:
            continue
        blob = f"{f.get('key', '')} {f.get('value', '')}".lower()
        for alias, target in catalog.items():
            if target == proj or len(alias) < 3:
                continue
            if alias in blob:
                out.append(
                    {
                        "from_project": proj,
                        "to_project": target,
                        "link_type": "related",
                        "strength": 55,
                        "reason": "稳定事实交叉引用",
                        "evidence": [f"事实 {f.get('key')}: {str(f.get('value', ''))[:80]}"],
                        "source": "inferred",
                    }
                )
    return out


def _infer_chat_hits(pids: list[str]) -> list[dict[str, Any]]:
    pid_set = set(pids)
    pair_count: dict[tuple[str, str], int] = {}
    with db.session() as conn:
        rows = conn.execute(
            "SELECT hits FROM chat_messages WHERE hits IS NOT NULL AND trim(hits)!='' "
            "ORDER BY created_at DESC LIMIT 400"
        ).fetchall()
    for r in rows:
        try:
            hits = json.loads(r["hits"])
        except (json.JSONDecodeError, TypeError):
            continue
        projs = {
            str(h.get("project") or "").strip()
            for h in hits
            if isinstance(h, dict) and h.get("project")
        }
        projs = {p for p in projs if p in pid_set}
        pl = sorted(projs)
        for i, a in enumerate(pl):
            for b in pl[i + 1 :]:
                pair_count[(a, b)] = pair_count.get((a, b), 0) + 1
    out: list[dict[str, Any]] = []
    for (a, b), c in pair_count.items():
        if c < 1:
            continue
        out.append(
            {
                "from_project": a,
                "to_project": b,
                "link_type": "related",
                "strength": min(75, 40 + c * 10),
                "reason": "问答检索同时命中多项目",
                "evidence": [f"chat hits 共现 ×{c}"],
                "source": "inferred",
            }
        )
    return out


def _merge_inferred(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        a, b = row["from_project"], row["to_project"]
        if a > b:
            a, b = b, a
        key = (a, b, row.get("link_type", "related"))
        ev = row.get("evidence") or []
        if key not in merged:
            merged[key] = {**row, "from_project": a, "to_project": b, "evidence": list(ev)}
            continue
        m = merged[key]
        m["strength"] = max(int(m.get("strength", 0)), int(row.get("strength", 0)))
        for e in ev:
            if e not in (m.get("evidence") or []):
                m.setdefault("evidence", []).append(e)
    return list(merged.values())


def infer_and_store(*, days: int = 30) -> dict[str, Any]:
    pids = _all_project_ids()
    catalog = _build_alias_catalog(pids)
    raw: list[dict[str, Any]] = []
    raw.extend(_infer_from_readme(pids, catalog))
    raw.extend(_infer_event_cooccurrence(pids, days=days))
    raw.extend(_infer_facts_cross(pids, catalog))
    raw.extend(_infer_chat_hits(pids))
    merged = _merge_inferred(raw)
    now = _now()
    inserted = updated = 0
    with db.session() as conn:
        for row in merged:
            existing = conn.execute(
                "SELECT id, pinned, source FROM project_links "
                "WHERE from_project=? AND to_project=? AND link_type=?",
                (row["from_project"], row["to_project"], row["link_type"]),
            ).fetchone()
            if existing and (existing["pinned"] or existing["source"] == "manual"):
                continue
            ev = json.dumps(row.get("evidence") or [], ensure_ascii=False)
            if existing:
                conn.execute(
                    "UPDATE project_links SET strength=?, reason=?, evidence=?, "
                    "source='inferred', updated_at=? WHERE id=?",
                    (
                        row["strength"],
                        row.get("reason"),
                        ev,
                        now,
                        existing["id"],
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO project_links "
                    "(from_project, to_project, link_type, strength, reason, evidence, "
                    "source, pinned, created_at, updated_at) VALUES (?,?,?,?,?,?,?,0,?,?)",
                    (
                        row["from_project"],
                        row["to_project"],
                        row["link_type"],
                        row["strength"],
                        row.get("reason"),
                        ev,
                        "inferred",
                        now,
                        now,
                    ),
                )
                inserted += 1
        db.set_state(conn, "project_relations_inferred_at", str(now))
    return {"inferred": len(merged), "inserted": inserted, "updated": updated, "at": now}


def _ensure_category_suites(conn) -> None:
    n = conn.execute("SELECT COUNT(*) c FROM project_suites").fetchone()["c"]
    if n:
        return
    grouped = workspace.list_projects_grouped(300)
    now = _now()
    cat_roles = {
        "dev": ("dev-stack", "日常开发", "主要开发与知识库相关项目", "product"),
        "tools": ("tools-stack", "工具脚本", "脚本、配置与自动化", "tooling"),
        "experiments": ("exp-stack", "实验探索", "原型与试验性项目", "experiment"),
        "mobile": ("mobile-stack", "移动端", "Android / 移动相关", "product"),
    }
    for i, cat in enumerate(grouped.get("categories") or []):
        slug, name, desc, role = cat_roles.get(
            cat, (f"cat-{cat}", f"{cat} 分类", f"工作区 {cat}/ 下项目", "legacy")
        )
        color = _DEFAULT_SUITE_COLORS[i % len(_DEFAULT_SUITE_COLORS)]
        conn.execute(
            "INSERT INTO project_suites(slug, name, description, role, color, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (slug, name, desc, role, color, i, now, now),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for j, p in enumerate((grouped.get("by_category") or {}).get(cat, [])):
            conn.execute(
                "INSERT OR IGNORE INTO project_suite_members(suite_id, project_id, sort_order) "
                "VALUES (?,?,?)",
                (sid, p["id"], j),
            )


def _node_brief(pid: str) -> dict[str, Any]:
    try:
        b = project_brief.brief(pid)
    except Exception:
        b = {}
    cat, name = workspace.parse_project_id(pid)
    return {
        "id": pid,
        "name": name,
        "category": cat or "",
        "purpose": (b.get("purpose") or "")[:200],
        "completion_label": b.get("completion_label") or "",
        "completion_pct": b.get("completion") if isinstance(b.get("completion"), (int, float)) else None,
    }


def build_graph(*, suite_id: int | None = None) -> dict[str, Any]:
    grouped = workspace.list_projects_grouped(300)
    pids = list(grouped.get("projects") or [])
    with db.session() as conn:
        _ensure_category_suites(conn)
        suites = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM project_suites ORDER BY sort_order, name"
            ).fetchall()
        ]
        members: dict[int, list[dict]] = {}
        for r in conn.execute(
            "SELECT suite_id, project_id, role_in_suite, note, sort_order "
            "FROM project_suite_members ORDER BY sort_order"
        ).fetchall():
            members.setdefault(r["suite_id"], []).append(dict(r))
        for s in suites:
            s["members"] = members.get(s["id"], [])
        metas = {
            r["project_id"]: dict(r)
            for r in conn.execute("SELECT * FROM project_meta").fetchall()
        }
        links = [dict(r) for r in conn.execute(
            "SELECT * FROM project_links ORDER BY strength DESC"
        ).fetchall()]
        inferred_at = db.get_state(conn, "project_relations_inferred_at")

    if suite_id is not None:
        suite = next((s for s in suites if s["id"] == suite_id), None)
        if suite:
            member_ids = {m["project_id"] for m in suite.get("members") or []}
            pids = [p for p in pids if p in member_ids]
            links = [
                l
                for l in links
                if l["from_project"] in member_ids and l["to_project"] in member_ids
            ]

    nodes = []
    for pid in pids:
        n = _node_brief(pid)
        meta = metas.get(pid) or {}
        n["role"] = meta.get("role") or ""
        n["note"] = meta.get("note") or ""
        n["suites"] = [
            s["name"]
            for s in suites
            if any(m["project_id"] == pid for m in (s.get("members") or []))
        ]
        for item in grouped.get("by_category", {}).get(n["category"], []):
            if item["id"] == pid:
                n["docs"] = item.get("docs", 0)
                break
        else:
            n["docs"] = 0
        nodes.append(n)

    edges = []
    for l in links:
        ev = l.get("evidence")
        if isinstance(ev, str):
            try:
                ev = json.loads(ev)
            except json.JSONDecodeError:
                ev = [ev] if ev else []
        edges.append(
            {
                "id": l["id"],
                "from": l["from_project"],
                "to": l["to_project"],
                "type": l["link_type"],
                "type_label": LINK_TYPE_LABELS.get(l["link_type"], l["link_type"]),
                "strength": l["strength"],
                "reason": l.get("reason") or "",
                "evidence": ev or [],
                "source": l.get("source") or "manual",
                "pinned": bool(l.get("pinned")),
            }
        )

    return {
        "nodes": nodes,
        "edges": edges,
        "suites": suites,
        "link_types": [{"id": t, "label": LINK_TYPE_LABELS[t]} for t in LINK_TYPES],
        "role_labels": ROLE_LABELS,
        "inferred_at": int(inferred_at) if inferred_at and inferred_at.isdigit() else None,
        "stats": {
            "projects": len(nodes),
            "links": len(edges),
            "suites": len(suites),
            "manual_links": sum(1 for e in edges if e.get("source") == "manual"),
            "inferred_links": sum(1 for e in edges if e.get("source") == "inferred"),
        },
    }


def upsert_link(
    *,
    from_project: str,
    to_project: str,
    link_type: str = "related",
    strength: int = 60,
    reason: str = "",
    evidence: list[str] | None = None,
    pinned: bool = True,
) -> dict[str, Any]:
    fp = workspace.normalize_project_id(from_project)
    tp = workspace.normalize_project_id(to_project)
    if fp == tp:
        raise ValueError("不能连接同一项目")
    if link_type not in LINK_TYPES:
        raise ValueError(f"无效 link_type: {link_type}")
    strength = max(1, min(100, int(strength)))
    now = _now()
    ev = json.dumps(evidence or [], ensure_ascii=False)
    with db.session() as conn:
        row = conn.execute(
            "SELECT id FROM project_links WHERE from_project=? AND to_project=? AND link_type=?",
            (fp, tp, link_type),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE project_links SET strength=?, reason=?, evidence=?, "
                "source='manual', pinned=?, updated_at=? WHERE id=?",
                (strength, reason, ev, 1 if pinned else 0, now, row["id"]),
            )
            lid = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO project_links "
                "(from_project, to_project, link_type, strength, reason, evidence, "
                "source, pinned, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (fp, tp, link_type, strength, reason, ev, "manual", 1 if pinned else 0, now, now),
            )
            lid = cur.lastrowid
    return {"id": lid, "from_project": fp, "to_project": tp, "link_type": link_type}


def delete_link(link_id: int) -> bool:
    with db.session() as conn:
        cur = conn.execute("DELETE FROM project_links WHERE id=?", (link_id,))
        return cur.rowcount > 0


def create_suite(
    *,
    name: str,
    description: str = "",
    role: str = "",
    color: str = "",
) -> dict[str, Any]:
    now = _now()
    slug = _slugify(name)
    with db.session() as conn:
        base = slug
        i = 0
        while conn.execute("SELECT 1 FROM project_suites WHERE slug=?", (slug,)).fetchone():
            i += 1
            slug = f"{base}-{i}"
        conn.execute(
            "INSERT INTO project_suites(slug, name, description, role, color, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (slug, name.strip(), description, role, color or _DEFAULT_SUITE_COLORS[0], 999, now, now),
        )
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": sid, "slug": slug, "name": name}


def update_suite(
    suite_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    role: str | None = None,
    color: str | None = None,
    sort_order: int | None = None,
) -> bool:
    fields: list[str] = []
    vals: list[Any] = []
    if name is not None:
        fields.append("name=?")
        vals.append(name.strip())
    if description is not None:
        fields.append("description=?")
        vals.append(description)
    if role is not None:
        fields.append("role=?")
        vals.append(role)
    if color is not None:
        fields.append("color=?")
        vals.append(color)
    if sort_order is not None:
        fields.append("sort_order=?")
        vals.append(sort_order)
    if not fields:
        return False
    fields.append("updated_at=?")
    vals.append(_now())
    vals.append(suite_id)
    with db.session() as conn:
        cur = conn.execute(
            f"UPDATE project_suites SET {', '.join(fields)} WHERE id=?",
            vals,
        )
        return cur.rowcount > 0


def delete_suite(suite_id: int) -> bool:
    with db.session() as conn:
        cur = conn.execute("DELETE FROM project_suites WHERE id=?", (suite_id,))
        return cur.rowcount > 0


def set_suite_members(suite_id: int, project_ids: list[str]) -> int:
    normalized = [workspace.normalize_project_id(p) for p in project_ids if p.strip()]
    with db.session() as conn:
        conn.execute("DELETE FROM project_suite_members WHERE suite_id=?", (suite_id,))
        for i, pid in enumerate(normalized):
            conn.execute(
                "INSERT INTO project_suite_members(suite_id, project_id, sort_order) VALUES (?,?,?)",
                (suite_id, pid, i),
            )
    return len(normalized)


def add_suite_member(suite_id: int, project_id: str, note: str = "") -> None:
    pid = workspace.normalize_project_id(project_id)
    with db.session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO project_suite_members(suite_id, project_id, note, sort_order) "
            "VALUES (?,?,?, COALESCE((SELECT MAX(sort_order)+1 FROM project_suite_members WHERE suite_id=?),0))",
            (suite_id, pid, note, suite_id),
        )


def remove_suite_member(suite_id: int, project_id: str) -> bool:
    pid = workspace.normalize_project_id(project_id)
    with db.session() as conn:
        cur = conn.execute(
            "DELETE FROM project_suite_members WHERE suite_id=? AND project_id=?",
            (suite_id, pid),
        )
        return cur.rowcount > 0


def set_project_meta(project_id: str, *, role: str = "", note: str = "") -> None:
    pid = workspace.normalize_project_id(project_id)
    now = _now()
    with db.session() as conn:
        conn.execute(
            "INSERT INTO project_meta(project_id, role, note, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET role=excluded.role, note=excluded.note, updated_at=excluded.updated_at",
            (pid, role, note, now),
        )
