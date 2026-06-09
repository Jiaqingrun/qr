"""引导语自动合并提炼：生成待确认提案，确认后写入正式引导语。"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import config, db, prompt_guides

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

SOURCE_INBOX = "inbox_session"
SOURCE_GUIDE = "guide"

_REFINE_SYSTEM = """你是 QR 本地知识库的引导语编辑。用户有多段 Cursor 问话（可能含重复、UI 微调、系统废话）。
请合并提炼为一条**可复用**的中文引导语，供以后直接粘贴给 AI 使用。

规则：
1. 去掉系统提示、Browser error、Briefly inform、Execute diff-tab、过短确认（如「做吧」「AB」）。
2. 保留：目标、关键约束、验收标准、重要决策；合并重复诉求。
3. body 用 Markdown，建议结构（按需省略空节）：
   ## 目标
   ## 约束
   ## 验收标准
   ## 标准问法
4. 全文（含标题感）不超过 450 中文字；标准问法一段即可复制使用。
5. type_slug 从以下选一：feature, debug, explain, refactor, test, docs, ops, governance, general

只输出一行 JSON，无 markdown 围栏：
{"title":"短标题≤40字","type_slug":"feature","summary":"一句话说明用途","body":"Markdown正文"}"""

_NOISE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.I)
    for p in (
        r"^briefly inform the user",
        r"^if the available mcp tools",
        r"^execute the selected diff-tab",
        r"^browser error to investigate",
        r"^ok\.?$",
        r"^a$",
        r"^ab$",
        r"^做吧$",
        r"^执行$",
        r"^升级吧$",
        r"^帮我启用$",
        r"^帮我再执行$",
        r"^查看新报告$",
        r"^列出来并帮我修改$",
    )
]

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    prompt_guides.ensure_schema(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS prompt_guide_proposals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_kind     TEXT NOT NULL,
            source_key      TEXT NOT NULL,
            fragment_ids    TEXT NOT NULL,
            replace_guide_id INTEGER,
            title           TEXT NOT NULL,
            body            TEXT NOT NULL,
            type_id         INTEGER,
            type_slug       TEXT,
            project         TEXT,
            summary         TEXT,
            status          TEXT NOT NULL DEFAULT 'pending',
            meta            TEXT,
            created_at      INTEGER NOT NULL,
            updated_at      INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pgp_status ON prompt_guide_proposals(status, updated_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pgp_pending_source
            ON prompt_guide_proposals(source_kind, source_key)
            WHERE status='pending';
        """
    )


def is_noise_content(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 3:
        return True
    if len(t) <= 4 and t.isascii():
        return True
    low = t.lower()
    return any(p.search(low) for p in _NOISE_PATTERNS)


def is_raw_merged_guide(guide: dict) -> bool:
    body = guide.get("body") or ""
    title = guide.get("title") or ""
    if guide.get("origin") != prompt_guides.ORIGIN_MERGED:
        return False
    if "## 片段" in body:
        return True
    if title.startswith("/Users/") or "cursor_chats" in title:
        return True
    fc = int(guide.get("fragment_count") or 0)
    return fc >= 2 and len(body) > 400


def _parse_distill_json(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError("模型未返回有效 JSON")
    data = json.loads(m.group())
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    if not title or not body:
        raise ValueError("提炼结果缺少 title 或 body")
    slug = str(data.get("type_slug") or "general").strip().lower()
    if slug not in {t["slug"] for t in prompt_guides.DEFAULT_TYPES}:
        slug = "general"
    return {
        "title": title[:80],
        "body": body[:4000],
        "type_slug": slug,
        "summary": str(data.get("summary") or "").strip()[:200],
    }


def _cap_fragments(frags: list[dict], max_n: int = 30) -> list[dict]:
    if len(frags) <= max_n:
        return frags
    head = max_n // 3
    tail = max_n - head
    return frags[:head] + frags[-tail:]


def _fragments_for_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT f.* FROM prompt_guide_fragments f "
        "WHERE f.guide_id IS NULL AND f.cursor_session_id=? ORDER BY f.ts, f.query_index",
        (session_id,),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        content = (row["content"] or "").strip()
        if is_noise_content(content):
            continue
        out.append(dict(row))
    return _cap_fragments(out)


def _fragments_for_guide(conn: sqlite3.Connection, guide_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT f.* FROM prompt_guide_fragments f WHERE f.guide_id=? ORDER BY f.ts, f.query_index",
        (guide_id,),
    ).fetchall()
    if rows:
        out = [dict(r) for r in rows if not is_noise_content((r["content"] or "").strip())]
        return _cap_fragments(out)
    guide = prompt_guides.get_guide(conn, guide_id)
    if not guide:
        return []
    body = guide.get("body") or ""
    parts = re.split(r"\n---\n", body)
    frags: list[dict] = []
    for part in parts:
        m = re.match(r"## 片段 \d+\n\n([\s\S]*)", part.strip())
        if not m:
            continue
        content = m.group(1).strip()
        if is_noise_content(content):
            continue
        frags.append({"id": None, "content": content, "project": guide.get("project")})
    return _cap_fragments(frags)


def _build_distill_prompt(
    frags: list[dict],
    *,
    hint: str = "",
    existing_title: str = "",
) -> str:
    lines = ["# 问话片段（按时间顺序）", ""]
    if hint:
        lines.extend([f"背景：{hint}", ""])
    if existing_title:
        lines.extend([f"当前标题：{existing_title}", ""])
    for i, f in enumerate(frags, 1):
        content = (f.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"## 片段 {i}\n\n{content[:2000]}")
        lines.append("")
    blob = "\n".join(lines)
    if len(blob) > 14000:
        blob = blob[:14000] + "\n\n…（后续片段已截断）"
    return blob


def distill_fragments(
    frags: list[dict],
    *,
    hint: str = "",
    existing_title: str = "",
    model: str | None = None,
) -> dict[str, str]:
    if not frags:
        raise ValueError("没有可提炼的有效片段")
    from .ollama_client import Ollama

    prompt = _build_distill_prompt(frags, hint=hint, existing_title=existing_title)
    raw = Ollama().generate(prompt, system=_REFINE_SYSTEM, model=model, strip_think=True)
    return _parse_distill_json(raw)


def _resolve_type_id(conn: sqlite3.Connection, slug: str) -> int:
    row = conn.execute(
        "SELECT id FROM prompt_guide_types WHERE slug=?", (slug,),
    ).fetchone()
    if row:
        return int(row["id"])
    row = conn.execute(
        "SELECT id FROM prompt_guide_types WHERE slug='general'",
    ).fetchone()
    return int(row["id"]) if row else 1


def _pending_for_source(conn: sqlite3.Connection, kind: str, key: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM prompt_guide_proposals WHERE source_kind=? AND source_key=? AND status=?",
        (kind, key, STATUS_PENDING),
    ).fetchone()
    return dict(row) if row else None


def _save_proposal(
    conn: sqlite3.Connection,
    *,
    source_kind: str,
    source_key: str,
    fragment_ids: list[int],
    replace_guide_id: int | None,
    distilled: dict[str, str],
    project: str | None,
    meta: dict[str, Any],
) -> dict:
    ensure_schema(conn)
    existing = _pending_for_source(conn, source_kind, source_key)
    now = db.now()
    type_id = _resolve_type_id(conn, distilled["type_slug"])
    meta_json = json.dumps(meta, ensure_ascii=False)
    if existing:
        conn.execute(
            "UPDATE prompt_guide_proposals SET fragment_ids=?, replace_guide_id=?, title=?, body=?, "
            "type_id=?, type_slug=?, project=?, summary=?, meta=?, updated_at=? WHERE id=?",
            (
                json.dumps(fragment_ids, ensure_ascii=False),
                replace_guide_id,
                distilled["title"],
                distilled["body"],
                type_id,
                distilled["type_slug"],
                project,
                distilled.get("summary") or "",
                meta_json,
                now,
                existing["id"],
            ),
        )
        pid = int(existing["id"])
    else:
        cur = conn.execute(
            "INSERT INTO prompt_guide_proposals"
            "(source_kind, source_key, fragment_ids, replace_guide_id, title, body, "
            "type_id, type_slug, project, summary, status, meta, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_kind,
                source_key,
                json.dumps(fragment_ids, ensure_ascii=False),
                replace_guide_id,
                distilled["title"],
                distilled["body"],
                type_id,
                distilled["type_slug"],
                project,
                distilled.get("summary") or "",
                STATUS_PENDING,
                meta_json,
                now,
                now,
            ),
        )
        pid = int(cur.lastrowid)
    conn.commit()
    return get_proposal(conn, pid) or {"id": pid}


def list_candidates(
    conn: sqlite3.Connection,
    *,
    include_inbox: bool = True,
    include_raw_guides: bool = True,
) -> list[dict[str, Any]]:
    ensure_schema(conn)
    out: list[dict[str, Any]] = []
    if include_inbox:
        grouped = prompt_guides.list_inbox_groups(conn, limit=800)
        for g in grouped.get("groups") or []:
            sid = g.get("session_id") or ""
            if not sid or sid == "unknown":
                continue
            frags = _fragments_for_session(conn, sid)
            if len(frags) < 1:
                continue
            if len(frags) == 1 and len(frags[0].get("content") or "") < 40:
                continue
            if _pending_for_source(conn, SOURCE_INBOX, sid):
                continue
            out.append({
                "source_kind": SOURCE_INBOX,
                "source_key": sid,
                "fragment_ids": [int(f["id"]) for f in frags if f.get("id")],
                "replace_guide_id": None,
                "project": g.get("project") or "",
                "fragment_count": len(frags),
                "hint": f"Cursor 对话 {sid[:8]}… · {len(frags)} 段有效问话",
            })
    if include_raw_guides:
        for g in prompt_guides.list_guides(conn, limit=200):
            if not is_raw_merged_guide(g):
                continue
            gid = int(g["id"])
            if _pending_for_source(conn, SOURCE_GUIDE, str(gid)):
                continue
            frags = _fragments_for_guide(conn, gid)
            if not frags:
                continue
            fids = [int(f["id"]) for f in frags if f.get("id")]
            out.append({
                "source_kind": SOURCE_GUIDE,
                "source_key": str(gid),
                "fragment_ids": fids,
                "replace_guide_id": gid,
                "project": g.get("project") or "",
                "fragment_count": len(frags),
                "hint": f"待精炼引导语 #{gid} · {g.get('title', '')[:40]}",
                "existing_title": g.get("title") or "",
            })
    out.sort(key=lambda x: (_candidate_sort_key(x), -x["fragment_count"]))
    return out


def _candidate_sort_key(c: dict[str, Any]) -> tuple[int, int]:
    fc = int(c.get("fragment_count") or 0)
    if c.get("source_kind") == SOURCE_INBOX:
        tier = 0
    elif fc <= 35:
        tier = 1
    elif fc <= 80:
        tier = 2
    else:
        tier = 3
    return (tier, fc)


def generate_proposals(
    conn: sqlite3.Connection,
    *,
    limit: int = 5,
    include_inbox: bool = True,
    include_raw_guides: bool = True,
    model: str | None = None,
) -> dict[str, Any]:
    cfg = config.load_config()
    cap = int(cfg.get("prompt_refine_max_proposals", limit) or limit)
    cap = max(1, min(cap, 20))
    candidates = list_candidates(
        conn, include_inbox=include_inbox, include_raw_guides=include_raw_guides,
    )
    total_candidates = len(candidates)
    to_process = candidates[:cap]
    created: list[dict] = []
    errors: list[str] = []
    for cand in to_process:
        try:
            if cand["source_kind"] == SOURCE_INBOX:
                frags = _fragments_for_session(conn, cand["source_key"])
            else:
                frags = _fragments_for_guide(conn, int(cand["source_key"]))
            distilled = distill_fragments(
                frags,
                hint=cand.get("hint") or "",
                existing_title=cand.get("existing_title") or "",
                model=model,
            )
            prop = _save_proposal(
                conn,
                source_kind=cand["source_kind"],
                source_key=cand["source_key"],
                fragment_ids=cand["fragment_ids"],
                replace_guide_id=cand.get("replace_guide_id"),
                distilled=distilled,
                project=cand.get("project") or None,
                meta={
                    "fragment_count": cand["fragment_count"],
                    "hint": cand.get("hint") or "",
                },
            )
            created.append(prop)
        except Exception as exc:
            errors.append(f"{cand['source_kind']}:{cand['source_key']}: {exc}")
    return {
        "created": len(created),
        "proposals": created,
        "candidates_total": total_candidates,
        "candidates_remaining": max(0, total_candidates - len(to_process)),
        "errors": errors,
    }


def list_proposals(
    conn: sqlite3.Connection,
    *,
    status: str = STATUS_PENDING,
    limit: int = 50,
) -> list[dict]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT p.*, t.name AS type_name FROM prompt_guide_proposals p "
        "LEFT JOIN prompt_guide_types t ON p.type_id=t.id "
        "WHERE p.status=? ORDER BY p.updated_at DESC LIMIT ?",
        (status, limit),
    ).fetchall()
    return [_enrich_proposal(dict(r)) for r in rows]


def get_proposal(conn: sqlite3.Connection, proposal_id: int) -> dict | None:
    row = conn.execute(
        "SELECT p.*, t.name AS type_name FROM prompt_guide_proposals p "
        "LEFT JOIN prompt_guide_types t ON p.type_id=t.id WHERE p.id=?",
        (proposal_id,),
    ).fetchone()
    if not row:
        return None
    return _enrich_proposal(dict(row))


def _enrich_proposal(d: dict) -> dict:
    if d.get("fragment_ids") and isinstance(d["fragment_ids"], str):
        try:
            d["fragment_ids"] = json.loads(d["fragment_ids"])
        except json.JSONDecodeError:
            d["fragment_ids"] = []
    if d.get("meta") and isinstance(d["meta"], str):
        try:
            d["meta"] = json.loads(d["meta"])
        except json.JSONDecodeError:
            d["meta"] = {}
    d["badges"] = {
        "source": {
            "label": "收件箱对话" if d.get("source_kind") == SOURCE_INBOX else "精炼已有",
            "kind": "inbox" if d.get("source_kind") == SOURCE_INBOX else "merged",
        },
        "status": {"label": "待确认", "kind": "inbox"},
    }
    return d


def approve_proposal(
    conn: sqlite3.Connection,
    proposal_id: int,
    *,
    title: str | None = None,
    body: str | None = None,
) -> dict:
    prop = get_proposal(conn, proposal_id)
    if not prop:
        raise ValueError("提案不存在")
    if prop["status"] != STATUS_PENDING:
        raise ValueError("该提案已处理")
    final_title = (title or prop["title"] or "").strip()
    final_body = (body or prop["body"] or "").strip()
    if not final_title or not final_body:
        raise ValueError("标题与正文不能为空")
    type_id = int(prop["type_id"]) if prop.get("type_id") else None
    fids = [int(x) for x in (prop.get("fragment_ids") or []) if x is not None]
    replace_id = prop.get("replace_guide_id")
    tags = ["refined", "auto-distill"]
    if replace_id:
        guide = prompt_guides.update_guide_content(
            conn,
            int(replace_id),
            title=final_title,
            body=final_body,
            type_id=type_id,
            tags=tags,
            refined=True,
        )
    elif fids:
        guide = prompt_guides.merge_fragments(
            conn,
            fids,
            title=final_title,
            type_id=type_id,
            project=prop.get("project"),
            tags=tags,
            body=final_body,
            refined=True,
        )
    else:
        guide = prompt_guides.create_guide_manual(
            conn,
            final_title,
            final_body,
            type_id=type_id,
            project=prop.get("project"),
            tags=tags,
        )
    now = db.now()
    conn.execute(
        "UPDATE prompt_guide_proposals SET status=?, title=?, body=?, updated_at=?, "
        "meta=? WHERE id=?",
        (
            STATUS_APPROVED,
            final_title,
            final_body,
            now,
            json.dumps(
                {**(prop.get("meta") or {}), "guide_id": guide.get("id")},
                ensure_ascii=False,
            ),
            proposal_id,
        ),
    )
    conn.commit()
    return {"proposal_id": proposal_id, "guide": guide}


def reject_proposal(conn: sqlite3.Connection, proposal_id: int) -> None:
    prop = get_proposal(conn, proposal_id)
    if not prop:
        raise ValueError("提案不存在")
    conn.execute(
        "UPDATE prompt_guide_proposals SET status=?, updated_at=? WHERE id=?",
        (STATUS_REJECTED, db.now(), proposal_id),
    )
    conn.commit()
