from __future__ import annotations

import re
from typing import Iterator

import numpy as np

from . import activity_context, chat, config, db, facts, hybrid, retrieval_meta, websearch
from .ollama_client import Ollama
from .vectors import cosine_topk, from_blob, to_blob

_REFUSE = (
    "根据 QR本地知识库 检索结果，未找到与该问题直接相关的项目资料，无法确定答案。"
    "请确认项目已纳入索引（qr index），或换一种问法。"
)

_PROJECT_STOP = {
    "next", "js", "vue", "web", "api", "http", "https", "qr", "the", "and", "or",
}

SYSTEM = (
    "你是用户的QR本地知识库助手。依据提供的【本地上下文】和（若有）【网络搜索结果】回答问题，"
    "标注引用来源（本地文件路径或网络链接）。若都没有答案，明确说明不知道，不要编造。用简体中文回答。"
)


def _is_qr_query(question: str) -> bool:
    ql = question.lower()
    hints = (
        "qr ", "qr.", "知识库", "qr本地", "qr本地知识库", "config.json", "launchd",
        "chat_sessions", "chat_messages", "context_tokens", "deep_context",
        "embed_model", "web_port", "web 服务", "web服务", "schedule install",
    )
    return any(h in ql for h in hints)


def _extract_project_refs(question: str) -> list[str]:
    if _is_qr_query(question) and "项目" not in question:
        return []
    refs: list[str] = []
    for m in re.finditer(r"([A-Za-z][\w-]{2,})\s*项目", question):
        refs.append(m.group(1).lower())
    if not refs:
        for m in re.finditer(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b", question, re.I):
            w = m.group(1).lower()
            if w not in _PROJECT_STOP and len(w) > 4:
                refs.append(w)
    out: list[str] = []
    for r in refs:
        if r not in out:
            out.append(r)
    return out


def _path_matches_project(path: str, ref: str) -> bool:
    p = path.lower().replace("_", "-")
    r = ref.lower().replace("_", "-")
    return r in p


def _project_filter_match(
    path: str,
    project: str | None,
    doc_project: str | None,
    category: str | None = None,
) -> bool:
    path_l = (path or "").lower().replace("\\", "/")
    doc_l = (doc_project or "").lower()
    if category:
        cl = category.lower()
        cat_ok = (
            f"/qr/{cl}/" in path_l
            or doc_l.startswith(f"{cl}/")
            or doc_l == cl
        )
        if not cat_ok:
            return False
    if not project:
        from . import workspace

        return workspace.is_searchable_content(path, doc_project)
    pl = project.lower()
    if "/" in pl:
        return pl in path_l or pl in doc_l
    return pl in path_l or pl in doc_l or doc_l.endswith(f"/{pl}")


def _project_context_mismatch(question: str, hits: list[dict]) -> str | None:
    refs = _extract_project_refs(question)
    if not refs:
        return None
    for ref in refs:
        if any(_path_matches_project(h["path"], ref) for h in hits):
            return None
    return _REFUSE


def _framework_evidence_insufficient(question: str, hits: list[dict]) -> bool:
    q = question.lower()
    ask_framework = ("前端框架" in question) or ("next.js" in q and "vue" in q) or ("还是" in question and "vue" in q)
    if not ask_framework:
        return False
    if not hits:
        return True
    good_path_hints = (
        "package.json", "next.config", "vite.config", "nuxt.config",
        "/src/", "/app/", ".vue", ".tsx", ".jsx",
    )
    for h in hits[:6]:
        p = (h.get("path") or "").lower()
        t = (h.get("text") or "").lower()
        if any(k in p for k in good_path_hints):
            return False
        if ("dependencies" in t or "devdependencies" in t) and ("next" in t or "vue" in t):
            return False
    return True


def _path_boost(path: str, question: str) -> float:
    p = path.lower().replace("\\", "/")
    boost = 0.0
    if _is_qr_query(question):
        if "/qr/dev/qr/" in p or "/projects/qr/" in p:
            boost += 0.18
        if "/.qr/" in p or p.endswith("config.json"):
            boost += 0.16
        for name in ("config.py", "cli.py", "chat.py", "web.py", "db.py"):
            if f"/qr/{name}" in p or f"/qr/qr/{name}" in p:
                boost += 0.08
                break
    for ref in _extract_project_refs(question):
        if _path_matches_project(path, ref):
            boost += 0.22
    if "/agent-transcripts/" in p or "cursor-" in p:
        boost += 0.05
    return boost


def _source_type_adjust(path: str, question: str) -> float:
    st = retrieval_meta.classify_source_type(path)
    q = question.lower()
    if _is_qr_query(question):
        if st == "transcript":
            return -0.06
        if st in ("config", "manifest", "code"):
            return 0.04
    if ("前端框架" in question) or ("package.json" in q):
        if st == "manifest":
            return 0.08
        if st == "transcript":
            return -0.08
    return 0.0


def _rerank_hits(hits: list[dict], question: str, k: int) -> list[dict]:
    out: list[dict] = []
    for h in hits:
        boost = _path_boost(h["path"], question) + _source_type_adjust(h["path"], question)
        out.append(retrieval_meta.annotate_hit(h, question, boost))
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:k]


def _search_vec(
    conn, qvec: list[float], k: int, project: str | None, category: str | None = None,
) -> list[dict]:
    db.sync_vec(conn)
    rows = conn.execute(
        "SELECT rowid, distance FROM vec_chunks "
        "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (to_blob(qvec), k),
    ).fetchall()
    out = []
    for r in rows:
        info = conn.execute(
            "SELECT c.text, d.path, d.project FROM chunks c "
            "JOIN documents d ON c.doc_id=d.id WHERE c.id=?",
            (r["rowid"],),
        ).fetchone()
        if info and _project_filter_match(
            info["path"], project, info["project"], category=category,
        ):
            out.append({
                "chunk_id": int(r["rowid"]),
                "score": 1.0 - float(r["distance"]),
                "path": info["path"],
                "project": info["project"],
                "text": info["text"],
            })
    return out


def _search_numpy(
    conn, qvec: list[float], k: int, project: str | None, category: str | None = None,
) -> list[dict]:
    rows = conn.execute(
        "SELECT c.id, c.text, c.embedding, d.path, d.project "
        "FROM chunks c JOIN documents d ON c.doc_id=d.id"
    ).fetchall()
    if not rows:
        return []
    idx_map: list[int] = []
    filtered = []
    for i, r in enumerate(rows):
        if _project_filter_match(r["path"], project, r["project"], category=category):
            idx_map.append(i)
            filtered.append(r)
    if not filtered:
        return []
    matrix = np.vstack([from_blob(r["embedding"]) for r in filtered])
    hits = cosine_topk(qvec, matrix, k)
    return [{
        "chunk_id": int(filtered[i]["id"]),
        "score": s,
        "path": filtered[i]["path"],
        "project": filtered[i]["project"],
        "text": filtered[i]["text"],
    } for i, s in hits]


_IDENT_QUERY = re.compile(r"^[\w.]{3,80}$")


def _symbol_hits(
    question: str,
    project: str | None,
    category: str | None,
    limit: int = 4,
) -> list[dict]:
    q = question.strip()
    if not _IDENT_QUERY.match(q):
        return []
    from . import symbol_index

    rows = symbol_index.search(q, project=project, limit=limit * 2)
    out: list[dict] = []
    for r in rows:
        if category:
            pl = (r.get("project") or "").lower()
            path_l = (r["path"] or "").lower()
            cl = category.lower()
            if not (pl.startswith(f"{cl}/") or f"/qr/{cl}/" in path_l):
                continue
        out.append({
            "chunk_id": 0,
            "score": 0.92,
            "path": r["path"],
            "project": r.get("project"),
            "text": f"[符号 {r['kind']}] {r['name']} — 第 {r['line']} 行",
            "symbol": True,
        })
        if len(out) >= limit:
            break
    return out


def search(
    question: str,
    k: int = 6,
    project: str | None = None,
    category: str | None = None,
) -> list[dict]:
    sym = _symbol_hits(question, project, category)
    qvec = Ollama().embed(question)
    fetch_k = max(k * 4, 24)
    with db.session() as conn:
        has = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
        if not has:
            return sym[:k]
        if db.vec_available():
            vec_hits = _search_vec(conn, qvec, fetch_k, project, category=category)
        else:
            vec_hits = _search_numpy(conn, qvec, fetch_k, project, category=category)
        fts_hits = hybrid.fts_search(conn, question, fetch_k, project, category=category)
        if fts_hits:
            merged = hybrid.rrf_merge(vec_hits, fts_hits, limit=fetch_k)
        else:
            merged = [{**h, "rrf": h.get("score", 0.0)} for h in vec_hits]
    from . import workspace

    for h in merged:
        h["project"] = workspace.sanitize_display_project(h.get("project"))
    cfg = config.load_config()
    expanded = _parent_expand(merged, int(cfg.get("parent_expand_chars", 400)))
    from . import reranker

    ranked = _rerank_hits(expanded, question, max(k * 2, 12))
    merged_ranked = reranker.rerank_hits(
        question, ranked, k, enabled=bool(cfg.get("rerank_enabled", True)),
    )
    if not sym:
        return merged_ranked
    seen = {h["path"] for h in sym}
    rest = [h for h in merged_ranked if h.get("path") not in seen]
    return (sym + rest)[:k]


def _parent_expand(hits: list[dict], extra_chars: int) -> list[dict]:
    """为命中 chunk 附加同文档相邻上下文。"""
    if not hits or extra_chars <= 0:
        return hits
    out: list[dict] = []
    with db.session() as conn:
        for h in hits:
            nh = dict(h)
            row = conn.execute(
                "SELECT c.ordinal, c.text, d.path FROM chunks c "
                "JOIN documents d ON c.doc_id=d.id WHERE c.id=?",
                (h.get("chunk_id"),),
            ).fetchone()
            if not row:
                out.append(nh)
                continue
            ord_i = int(row["ordinal"])
            prev_t = conn.execute(
                "SELECT text FROM chunks WHERE doc_id=("
                "SELECT doc_id FROM chunks WHERE id=?) AND ordinal=?",
                (h["chunk_id"], ord_i - 1),
            ).fetchone()
            next_t = conn.execute(
                "SELECT text FROM chunks WHERE doc_id=("
                "SELECT doc_id FROM chunks WHERE id=?) AND ordinal=?",
                (h["chunk_id"], ord_i + 1),
            ).fetchone()
            parts = []
            if prev_t:
                parts.append((prev_t["text"] or "")[-extra_chars // 2:])
            parts.append(h.get("text", ""))
            if next_t:
                parts.append((next_t["text"] or "")[: extra_chars // 2])
            nh["text"] = "\n".join(p for p in parts if p).strip()
            out.append(nh)
    return out


def workspace_list_projects(limit: int = 200) -> dict:
    from . import workspace

    return workspace.list_projects_grouped(limit)


def list_categories(limit: int = 200) -> list[str]:
    return workspace_list_projects(limit)["categories"]


def prepare_ask(
    question: str,
    k: int = 6,
    model: str | None = None,
    web: bool = False,
    history: list[dict] | None = None,
    project: str | None = None,
    category: str | None = None,
) -> dict:
    hits = search(question, k, project=project, category=category)
    web_results: list[dict] = []
    if web:
        web_results = websearch.search(question, int(config.load_config().get("web_results", 5)))

    similar = chat.find_similar_questions(question, limit=3)
    facts_block = facts.prompt_block(project)
    activity_block = activity_context.prompt_block(question)

    mismatch = _project_context_mismatch(question, hits)
    if mismatch and not web_results and not history:
        return {
            "prompt": "",
            "system": SYSTEM,
            "model": model,
            "hits": hits,
            "web_results": web_results,
            "similar": similar,
            "early_answer": mismatch,
        }

    if _framework_evidence_insufficient(question, hits) and not web_results:
        return {
            "prompt": "",
            "system": SYSTEM,
            "model": model,
            "hits": hits,
            "web_results": web_results,
            "similar": similar,
            "early_answer": "当前检索结果缺少可直接判断前端框架的证据（如 package.json、next/vite 配置或前端源码），无法确定。",
        }

    if not hits and not web_results and not history and not activity_block:
        return {
            "prompt": "",
            "system": SYSTEM,
            "model": model,
            "hits": hits,
            "web_results": web_results,
            "similar": similar,
            "early_answer": "QR本地知识库为空且未检索到内容。请先 `qr index`，或加 --web 联网搜索。",
        }

    parts = []
    if activity_block:
        parts.append("【近期行为摘要】\n" + activity_block)
    if facts_block:
        parts.append(facts_block)
    if similar:
        parts.append(
            "【相似历史提问】\n"
            + "\n".join(f"- {s['title']}（{s['updated']}）" for s in similar)
        )
    if history:
        hist = []
        for m in history:
            role = "用户" if m.get("role") == "user" else "助手"
            hist.append(f"{role}: {m.get('content', '')}")
        parts.append("【对话历史】\n" + "\n\n".join(hist))
    if hits:
        guard = ""
        refs = _extract_project_refs(question)
        if refs:
            guard = (
                "注意：问题涉及特定项目（"
                + "、".join(refs)
                + "）。仅当上下文路径明确属于该项目时才作答；否则必须说明不知道，"
                "不得用其他项目的资料推断。\n\n"
            )
        parts.append("【本地上下文】\n" + guard + "\n\n".join(
            f"[来源 {i+1}] {h['path']}"
            + (f" · {h['project']}" if h.get("project") else "")
            + f"\n{h['text']}"
            for i, h in enumerate(hits)))
    if web_results:
        parts.append("【网络搜索结果】\n" + "\n\n".join(
            f"[网络 {i+1}] {w['title']} ({w['url']})\n{w['snippet']}"
            for i, w in enumerate(web_results)))
    if not parts:
        return {
            "prompt": "",
            "system": SYSTEM,
            "model": model,
            "hits": hits,
            "web_results": web_results,
            "similar": similar,
            "early_answer": "QR本地知识库为空且未检索到内容。请先 `qr index`，或加 --web 联网搜索。",
        }

    prompt = (
        "\n\n".join(parts)
        + f"\n\n【当前问题】\n{question}\n\n"
        + (
            "请依据【近期行为摘要】归纳用户最近做了哪些事，按主题分条说明，"
            "可提及主要项目、Git/Cursor/Shell 与应用使用；数据不足处如实说明。"
            if activity_block
            else "请结合对话历史（若有）与以上信息回答，并标注引用的来源编号（本地用[来源N]，网络用[网络N]）。"
        )
    )
    return {
        "prompt": prompt,
        "system": SYSTEM,
        "model": model,
        "hits": hits,
        "web_results": web_results,
        "similar": similar,
        "early_answer": None,
    }


def ask(
    question: str,
    k: int = 6,
    model: str | None = None,
    web: bool = False,
    history: list[dict] | None = None,
    project: str | None = None,
    category: str | None = None,
) -> tuple[str, list[dict], list[dict]]:
    ctx = prepare_ask(question, k, model, web, history, project, category=category)
    if ctx["early_answer"]:
        return ctx["early_answer"], ctx["hits"], ctx["web_results"]
    answer = Ollama().generate(ctx["prompt"], system=ctx["system"], model=model)
    return answer, ctx["hits"], ctx["web_results"]


def ask_stream(
    question: str,
    k: int = 6,
    model: str | None = None,
    web: bool = False,
    history: list[dict] | None = None,
    project: str | None = None,
    category: str | None = None,
) -> Iterator[dict]:
    yield {"type": "status", "text": "正在检索本地上下文…"}
    ctx = prepare_ask(question, k, model, web, history, project, category=category)
    yield {
        "type": "meta",
        "hits": ctx["hits"],
        "web": ctx["web_results"],
        "similar": ctx["similar"],
    }
    if ctx["early_answer"]:
        yield {"type": "status", "text": "正在生成回答…"}
        yield {"type": "token", "text": ctx["early_answer"]}
        yield {"type": "done", "answer": ctx["early_answer"]}
        return
    yield {"type": "status", "text": "正在生成回答…"}
    buf: list[str] = []
    for token in Ollama().generate_stream(ctx["prompt"], system=ctx["system"], model=model):
        buf.append(token)
        yield {"type": "token", "text": token}
    answer = "".join(buf)
    yield {"type": "done", "answer": answer}
