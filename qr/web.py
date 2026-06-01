from __future__ import annotations

import datetime
import json
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import (
    alerts,
    backfill,
    chat,
    collectors,
    compliance,
    config,
    context_meter,
    db,
    digest,
    eval_suite,
    export,
    facts,
    governance,
    indexer,
    links,
    project_panel,
    query,
    summary,
    usage,
    workspace,
)
from .collectors import notes
from .ollama_client import Ollama, OllamaError

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="QR本地知识库")


class AskBody(BaseModel):
    question: str
    k: int = 6
    deep: bool = False
    web: bool = False
    session_id: int | None = None
    project: str | None = None
    category: str | None = None
    stream: bool = True


class QueryBody(BaseModel):
    text: str
    k: int = 6
    project: str | None = None
    category: str | None = None


class LogBody(BaseModel):
    text: str
    tags: str | None = None
    kind: str = "note"


class SummaryBody(BaseModel):
    period: str = "week"
    date_from: str | None = None
    date_to: str | None = None


class StandardsBody(BaseModel):
    content: str
    note: str = "Web 编辑"


class IndexBody(BaseModel):
    reindex: bool = False


class EvalActionBody(BaseModel):
    actions: list[str]


class FactBody(BaseModel):
    key: str
    value: str
    project: str | None = None


class EvalCaseBody(BaseModel):
    id: str
    q: str
    must: list[str] = []
    nice: list[str] = []
    negative: bool = False


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    db.init_db()
    with db.session() as conn:
        ev = {r["source"]: r["c"] for r in conn.execute(
            "SELECT source, COUNT(*) c FROM events GROUP BY source").fetchall()}
        docs = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        chunks = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
        summ = conn.execute("SELECT COUNT(*) c FROM summaries").fetchone()["c"]
        chats = conn.execute("SELECT COUNT(*) c FROM chat_sessions").fetchone()["c"]
    backend = "sqlite-vec" if db.vec_available() else "numpy"
    try:
        models = Ollama().health()
    except OllamaError:
        models = []
    return {"events": ev, "documents": docs, "chunks": chunks,
            "summaries": summ, "chats": chats, "backend": backend, "models": models,
            "qr_home": str(config.QR_HOME)}


class OpenBody(BaseModel):
    path: str


def _parse_day(s: str) -> datetime.datetime:
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError("日期格式应为 YYYY-MM-DD") from e


def _day_start(day: datetime.datetime) -> int:
    return int(time.mktime(day.timetuple()))


def _day_end_exclusive(day: datetime.datetime) -> int:
    return int(time.mktime((day + datetime.timedelta(days=1)).timetuple()))


@app.get("/api/events")
def events(
    limit: int = 50,
    page: int = 1,
    source: str | None = None,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    db.init_db()
    limit = max(1, min(limit, 100))
    page = max(1, page)

    where: list[str] = []
    args: list = []
    if source:
        where.append("source=?")
        args.append(source)

    try:
        if date:
            day = _parse_day(date)
            where.append("ts>=? AND ts<?")
            args.extend([_day_start(day), _day_end_exclusive(day)])
        elif date_from or date_to:
            if date_from:
                d_from = _parse_day(date_from)
            else:
                d_from = None
            if date_to:
                d_to = _parse_day(date_to)
            else:
                d_to = None
            if d_from and d_to and d_from > d_to:
                return JSONResponse({"error": "起始日期不能晚于结束日期"}, status_code=400)
            if d_from:
                where.append("ts>=?")
                args.append(_day_start(d_from))
            if d_to:
                where.append("ts<?")
                args.append(_day_end_exclusive(d_to))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM events{clause}", args).fetchone()["c"]

    pages = max(1, (total + limit - 1) // limit) if total else 1
    page = min(page, pages)
    offset = (page - 1) * limit

    with db.session() as conn:
        rows = conn.execute(
            f"SELECT ts, source, project, title, content FROM events{clause} "
            f"ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()

    out = []
    for r in rows:
        item = {
            "ts": r["ts"],
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])),
            "source": r["source"],
            "project": r["project"],
            "title": r["title"],
            "content": r["content"],
        }
        link = links.event_link(r["source"], r["title"], r["content"], r["project"])
        if link:
            item["link"] = link
        out.append(item)

    return {
        "items": out,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
        "date": date,
        "date_from": date_from,
        "date_to": date_to,
        "source": source,
    }


@app.post("/api/open")
def api_open(body: OpenBody):
    try:
        links.open_path(body.path)
        return {"ok": True}
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    except subprocess.CalledProcessError as e:
        return JSONResponse({"error": f"打开失败: {e}"}, status_code=500)
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.post("/api/query")
def api_query(body: QueryBody):
    try:
        return {
            "hits": query.search(
                body.text, body.k, project=body.project, category=body.category,
            ),
        }
    except OllamaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/projects")
def api_projects(category: str | None = None):
    data = query.workspace_list_projects()
    if category:
        cat = category.strip().lower()
        projs = [p for p in data["projects"] if p.lower().startswith(f"{cat}/")]
        bc = data["by_category"]
        cat_key = next((k for k in bc if k.lower() == cat), cat)
        data = {
            **data,
            "projects": projs,
            "by_category": {cat_key: bc.get(cat_key, [])},
            "categories": [cat_key] if cat_key in bc or projs else [cat],
        }
    return data


@app.get("/api/categories")
def api_categories():
    cfg = config.load_config()
    indexed = query.list_categories()
    configured = workspace.categories(cfg)
    merged = list(dict.fromkeys([*configured, *indexed]))
    return {"categories": merged, "workspace_root": str(workspace.workspace_root(cfg))}


@app.get("/api/workspace/project/delete-preview")
def api_project_delete_preview(project: str):
    if not project.strip():
        return JSONResponse({"error": "缺少 project 参数"}, status_code=400)
    try:
        return workspace.preview_project_delete(project.strip())
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/workspace/project/delete")
def api_project_delete(body: DeleteProjectBody):
    try:
        return workspace.purge_project(
            body.project.strip(),
            confirm=body.confirm,
            confirm_phrase=body.confirm_phrase,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


def _finish_ask(body: AskBody, answer: str, hits, web_results, sid: int):
    with db.session() as conn:
        chat.add_user_message(conn, sid, body.question.strip())
        msg_id = chat.add_assistant_message(
            conn, sid, answer, hits=hits or None, web=web_results or None,
        )
        chat.touch_session(conn, sid)
        session = chat.get_session(conn, sid)
        history_after = chat.history_for_prompt(conn, sid)
        last_hits, last_web = hits, web_results
        ctx = context_meter.estimate_ask_context(
            history=history_after,
            question=body.question.strip(),
            k=body.k,
            web=body.web,
            deep=body.deep,
            hits=last_hits,
            web_results=last_web,
        )
    similar = chat.find_similar_questions(body.question.strip())
    if query._is_qr_query(body.question.strip()):
        facts.extract_from_text(answer, project=body.project or "QR")
    return {
        "answer": answer,
        "hits": hits,
        "web": web_results,
        "similar": similar,
        "session_id": sid,
        "message_id": msg_id,
        "context": ctx,
        "session": {
            "id": session["id"],
            "title": session["title"],
            "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(session["updated_at"])),
        },
    }


@app.post("/api/ask")
def api_ask(body: AskBody):
    db.init_db()
    model = config.load_config()["deep_model"] if body.deep else None
    question = body.question.strip()
    if not question:
        return JSONResponse({"error": "问题不能为空"}, status_code=400)

    history = None
    sid = body.session_id
    with db.session() as conn:
        if sid:
            session = chat.get_session(conn, sid)
            if session is None:
                return JSONResponse({"error": "对话不存在"}, status_code=404)
            history = chat.history_for_prompt(conn, sid)
        else:
            sid = chat.create_session(conn, title=question, deep=body.deep, web=body.web)

    if body.stream:
        import json as _json

        def gen():
            answer = ""
            hits: list = []
            web_results: list = []
            try:
                for ev in query.ask_stream(
                    question, body.k, model=model, web=body.web,
                    history=history, project=body.project, category=body.category,
                ):
                    if ev["type"] == "meta":
                        hits = ev.get("hits") or []
                        web_results = ev.get("web") or []
                        yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
                    elif ev["type"] == "token":
                        answer += ev.get("text", "")
                        yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
                    elif ev["type"] == "done":
                        answer = ev.get("answer") or answer
                        payload = _finish_ask(body, answer, hits, web_results, sid)
                        yield f"data: {_json.dumps({'type': 'done', **payload}, ensure_ascii=False)}\n\n"
            except OllamaError as e:
                yield f"data: {_json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    try:
        answer, hits, web_results = query.ask(
            question, body.k, model=model, web=body.web,
            history=history, project=body.project, category=body.category,
        )
    except OllamaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return _finish_ask(body, answer, hits, web_results, sid)


@app.get("/api/chats")
def api_chats(
    limit: int = 20,
    page: int = 1,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
):
    db.init_db()
    limit = max(1, min(limit, 50))
    page = max(1, page)

    d_from = d_to = None
    try:
        if date_from:
            d_from = _day_start(_parse_day(date_from))
        if date_to:
            d_to = _day_end_exclusive(_parse_day(date_to))
        if d_from and d_to and _parse_day(date_from) > _parse_day(date_to):
            return JSONResponse({"error": "起始日期不能晚于结束日期"}, status_code=400)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    with db.session() as conn:
        items, total = chat.list_sessions(
            conn, limit=limit, page=page,
            date_from=d_from, date_to=d_to,
            q=q.strip() if q else None,
        )

    pages = max(1, (total + limit - 1) // limit) if total else 1
    page = min(page, pages)
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
        "date_from": date_from,
        "date_to": date_to,
        "q": q,
    }


@app.get("/api/chats/{sid}")
def api_chat_detail(sid: int):
    db.init_db()
    with db.session() as conn:
        session = chat.get_session(conn, sid)
        if session is None:
            return JSONResponse({"error": "对话不存在"}, status_code=404)
        messages = chat.get_messages(conn, sid)
        turns = sum(1 for m in messages if m["role"] == "user")
        history = chat.history_for_prompt(conn, sid)
        last_hits, last_web = None, None
        for m in reversed(messages):
            if m["role"] == "assistant":
                last_hits, last_web = m.get("hits"), m.get("web")
                break
        ctx = context_meter.estimate_ask_context(
            history=history,
            k=6,
            web=session["web"],
            deep=session["deep"],
            hits=last_hits,
            web_results=last_web,
        )

    return {
        "id": session["id"],
        "title": session["title"],
        "deep": session["deep"],
        "web": session["web"],
        "turns": turns,
        "context": ctx,
        "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(session["created_at"])),
        "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(session["updated_at"])),
        "messages": messages,
    }


@app.get("/api/chats/{sid}/context")
def api_chat_context(
    sid: int,
    question: str = "",
    k: int = 6,
    deep: bool | None = None,
    web: bool | None = None,
):
    db.init_db()
    with db.session() as conn:
        session = chat.get_session(conn, sid)
        if session is None:
            return JSONResponse({"error": "对话不存在"}, status_code=404)
        history = chat.history_for_prompt(conn, sid)
        messages = chat.get_messages(conn, sid)
        last_hits, last_web = None, None
        for m in reversed(messages):
            if m["role"] == "assistant":
                last_hits, last_web = m.get("hits"), m.get("web")
                break
    use_deep = session["deep"] if deep is None else deep
    use_web = session["web"] if web is None else web
    ctx = context_meter.estimate_ask_context(
        history=history,
        question=question.strip(),
        k=max(1, min(k, 20)),
        web=use_web,
        deep=use_deep,
        hits=last_hits,
        web_results=last_web,
    )
    return ctx


@app.delete("/api/chats/{sid}")
def api_chat_delete(sid: int):
    db.init_db()
    with db.session() as conn:
        if not chat.delete_session(conn, sid):
            return JSONResponse({"error": "对话不存在"}, status_code=404)
    return {"ok": True}


@app.post("/api/log")
def api_log(body: LogBody):
    db.init_db()
    with db.session() as conn:
        notes.add_note(conn, body.text, tags=body.tags, kind=body.kind)
    return {"ok": True}


@app.post("/api/ingest")
def api_ingest():
    db.init_db()
    with db.session() as conn:
        res = collectors.run(conn, ["shell", "git", "files", "cursor"])
    return {"ingested": res}


@app.post("/api/backfill")
def api_backfill(days: int = 365):
    db.init_db()
    with db.session() as conn:
        res = backfill.run(conn, days=days)
    total = sum(v for k, v in res.items() if isinstance(v, int))
    return {"result": res, "total": total}


@app.post("/api/ingest/cursor")
def api_ingest_cursor(backfill: bool = False, days: int = 365):
    db.init_db()
    with db.session() as conn:
        if backfill:
            res = backfill.run(conn, days=days, sources=["cursor"])
            n = res.get("cursor", 0)
        else:
            from .collectors import cursor as cursor_col
            n = cursor_col.collect(conn)
    return {"ingested": n, "backfill": backfill, "days": days if backfill else None}


@app.post("/api/index")
def api_index(body: IndexBody | None = None):
    db.init_db()
    req = body or IndexBody()
    try:
        return {"stats": indexer.index(reindex=req.reindex)}
    except OllamaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/summaries")
def api_summaries(
    limit: int = 20,
    page: int = 1,
    period: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    db.init_db()
    limit = max(1, min(limit, 50))
    page = max(1, page)

    where: list[str] = []
    args: list = []
    if period:
        where.append("period=?")
        args.append(period)
    try:
        if date_from:
            where.append("end_ts>=?")
            args.append(_day_start(_parse_day(date_from)))
        if date_to:
            where.append("end_ts<?")
            args.append(_day_end_exclusive(_parse_day(date_to)))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM summaries{clause}", args).fetchone()["c"]

    pages = max(1, (total + limit - 1) // limit) if total else 1
    page = min(page, pages)
    offset = (page - 1) * limit

    with db.session() as conn:
        rows = conn.execute(
            f"SELECT id, period, start_ts, end_ts, content, created_at FROM summaries{clause} "
            f"ORDER BY end_ts DESC, id DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()

    items = []
    for r in rows:
        preview = (r["content"] or "").strip().replace("\n", " ")[:220]
        items.append({
            "id": r["id"],
            "period": r["period"],
            "start_date": time.strftime("%Y-%m-%d", time.localtime(r["start_ts"])),
            "end_date": time.strftime("%Y-%m-%d", time.localtime(r["end_ts"])),
            "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"])),
            "preview": preview,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
        "period": period,
        "date_from": date_from,
        "date_to": date_to,
    }


@app.get("/api/summaries/{sid}")
def api_summary_detail(sid: int):
    db.init_db()
    with db.session() as conn:
        r = conn.execute(
            "SELECT id, period, start_ts, end_ts, content, created_at FROM summaries WHERE id=?",
            (sid,),
        ).fetchone()
    if r is None:
        return JSONResponse({"error": "总结不存在"}, status_code=404)
    return {
        "id": r["id"],
        "period": r["period"],
        "start_date": time.strftime("%Y-%m-%d", time.localtime(r["start_ts"])),
        "end_date": time.strftime("%Y-%m-%d", time.localtime(r["end_ts"])),
        "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"])),
        "content": r["content"],
    }


@app.post("/api/summary")
def api_summary(body: SummaryBody):
    db.init_db()
    try:
        if body.date_from or body.date_to:
            if not body.date_from or not body.date_to:
                return JSONResponse({"error": "自定义总结需同时填写起始与结束日期"}, status_code=400)
            out = summary.generate(date_from=body.date_from, date_to=body.date_to)
        else:
            out = summary.generate(body.period)
        return {"path": str(out), "content": out.read_text(encoding="utf-8")}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except OllamaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


class ReviseBody(BaseModel):
    period: str = "week"


class ActivateStandardsBody(BaseModel):
    version_id: int
    note: str | None = None


class DeleteProjectBody(BaseModel):
    project: str
    confirm: str
    confirm_phrase: str


@app.get("/api/standards")
def api_standards():
    governance.ensure_standards()
    return {"content": governance.read_standards(), "versions": governance.list_versions()}


@app.get("/api/standards/version/{vid}")
def api_standards_version(vid: int):
    content = governance.get_version(vid)
    if content is None:
        return JSONResponse({"error": "版本不存在"}, status_code=404)
    return {"content": content}


@app.post("/api/standards/revise")
def api_standards_revise(body: ReviseBody):
    try:
        content = governance.revise_from_behavior(body.period)
        return {"content": content, "versions": governance.list_versions()}
    except OllamaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/standards/restore")
def api_standards_restore():
    content = governance.restore_standards_from_template(note="Web 恢复标准模板")
    return {"content": content, "versions": governance.list_versions()}


@app.post("/api/standards/activate")
def api_standards_activate(body: ActivateStandardsBody):
    try:
        content = governance.activate_version(body.version_id, note=body.note)
        return {"content": content, "versions": governance.list_versions()}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.put("/api/standards")
def api_standards_save(body: StandardsBody):
    governance.save_standards(body.content, note=body.note)
    return {"ok": True, "versions": governance.list_versions()}


@app.get("/api/digest")
def api_digest(days: int = 1):
    return digest.generate(days=max(1, min(days, 30)))


@app.post("/api/digest/notify")
def api_digest_notify(days: int = 1):
    return alerts.publish_digest(days=max(1, min(days, 30)), notify=True)


@app.get("/api/project")
def api_project_panel(project: str, days: int = 14):
    if not project.strip():
        return JSONResponse({"error": "缺少 project 参数"}, status_code=400)
    return project_panel.panel(project.strip(), days=max(1, min(days, 90)))


@app.get("/api/facts")
def api_facts_list(project: str | None = None):
    return {"facts": facts.list_facts(project)}


@app.post("/api/facts")
def api_facts_add(body: FactBody):
    row = facts.add_fact(body.key, body.value, project=body.project)
    return {"ok": True, "fact": row}


@app.post("/api/facts/sync")
def api_facts_sync():
    synced = facts.sync_from_config()
    return {"ok": True, "facts": synced}


@app.get("/api/eval/cases")
def api_eval_cases():
    return {"cases": eval_suite.load_cases(), "custom_path": str(eval_suite.CASES_PATH)}


@app.post("/api/eval/cases")
def api_eval_cases_add(body: EvalCaseBody):
    case = body.model_dump()
    eval_suite.save_custom_case(case)
    return {"ok": True, "cases": eval_suite.load_cases()}


@app.get("/api/eval/regression")
def api_eval_regression(limit: int = 8):
    return eval_suite.regression_report(limit=max(2, min(limit, 30)))


@app.post("/api/eval/decision-draft")
def api_eval_decision_draft():
    base = api_eval_failures()
    items = base.get("items", [])
    lines = ["# 决策记录", "", "## 问题", "RAG 评测出现失败项，需确定修复策略。", ""]
    if items:
        lines.append("## 失败摘要")
        for it in items:
            lines.append(f"- [{it.get('model')}] {it.get('case')}: {it.get('question')}")
        lines.extend(["", "## 选项", "- 仅 reindex", "- 调整检索权重/guard", "- 补充索引与文档", ""])
    lines.extend([
        "## 结论", "", "## 原因", "",
        "## 关联建议",
        *([f"- {s}" for it in items for s in (it.get("suggestions") or [])][:8]),
    ])
    return {"text": "\n".join(lines)}


@app.get("/api/compliance")
def api_compliance():
    return {"results": compliance.scan_index_roots()}


@app.get("/api/graph")
def api_graph(limit: int = 40):
    return compliance.knowledge_graph(limit=max(10, min(limit, 100)))


@app.post("/api/export/obsidian")
def api_export_obsidian():
    dest = export.export_obsidian()
    return {"path": str(dest)}


@app.get("/api/eval")
def api_eval():
    p = config.LOGS_DIR / "model_eval.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    import sys

    return {
        "message": f"尚无评测结果，运行: {sys.executable} {config.REPO_ROOT / 'scripts' / 'model_eval.py'}",
    }


@app.get("/api/eval/history")
def api_eval_history(limit: int = 12):
    limit = max(1, min(limit, 60))
    files = sorted(config.LOGS_DIR.glob("model_eval-*.json"), reverse=True)[:limit]
    items = []
    for p in files:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        qwen = data.get("results", {}).get("qwen", [])
        deep = data.get("results", {}).get("deepseek", [])
        q_ok = sum(1 for r in qwen if r.get("must_pass"))
        d_ok = sum(1 for r in deep if r.get("must_pass"))
        items.append({
            "file": p.name,
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
            "qwen_ok": q_ok,
            "deep_ok": d_ok,
            "total": max(len(qwen), len(deep), 1),
        })
    return {"items": items}


def _suggestions_for_case(case_id: str, answer_preview: str, hits: list[dict]) -> list[str]:
    tips: list[str] = []
    hit_paths = " ".join((h.get("path") or "").lower() for h in (hits or [])[:5])
    ap = (answer_preview or "").lower()
    if case_id == "negative":
        tips.append("为项目问答启用更严格的项目证据门槛（无 package.json/前端源码时拒答）")
        tips.append("将 transcript 类文档在框架识别场景降权，优先 package.json 与框架配置文件")
    if "不知道" in ap or "无法" in ap:
        tips.append("补充索引覆盖：检查目标配置文件是否已纳入索引并完成 reindex")
    if "nomic" in ap or "默认" in ap:
        tips.append("清理过时文档：统一 README 与运行时 config 的默认值")
    if "agent-transcripts" in hit_paths:
        tips.append("为问答增加 source_type 过滤：配置类问题优先代码/配置文档，降低 transcript 权重")
    if not tips:
        tips.append("检查该题 top hits 是否为目标项目核心文件，必要时加路径 boost")
        tips.append("为该类问题增加规则化 guard（证据不足即拒答）")
    return tips


@app.get("/api/eval/failures")
def api_eval_failures():
    p = config.LOGS_DIR / "model_eval.json"
    if not p.exists():
        return {"items": [], "summary": "尚无评测结果"}
    data = json.loads(p.read_text(encoding="utf-8"))
    out: list[dict] = []
    for model_key in ("qwen", "deepseek"):
        for r in data.get("results", {}).get(model_key, []):
            if r.get("must_pass"):
                continue
            out.append({
                "model": model_key,
                "case": r.get("case"),
                "question": r.get("question"),
                "answer_preview": (r.get("answer_preview") or "")[:300],
                "top_hits": r.get("top_hits") or [],
                "retrieval_ok": bool(r.get("retrieval_ok")),
                "suggestions": _suggestions_for_case(
                    r.get("case") or "",
                    r.get("answer_preview") or "",
                    r.get("top_hits") or [],
                ),
            })
    if not out:
        return {"items": [], "summary": "最近一次评测无失败题（全通过）"}
    return {"items": out, "summary": f"最近一次评测共 {len(out)} 个失败项"}


@app.get("/api/eval/fixplan")
def api_eval_fixplan():
    base = api_eval_failures()
    items = base.get("items", [])
    if not items:
        return {
            "summary": "当前无失败题，无需自动修复。",
            "actions": [],
            "checklist": [],
        }
    actions: list[dict] = []
    action_ids: set[str] = set()
    checklist: list[str] = []
    for it in items:
        case_id = (it.get("case") or "").lower()
        if case_id in {"negative", "framework"} and "tighten_framework_guard" not in action_ids:
            actions.append({
                "id": "tighten_framework_guard",
                "title": "强化框架识别证据门槛",
                "why": "避免无直接证据时误判 Next/Vue",
                "auto": False,
            })
            action_ids.add("tighten_framework_guard")
        if not it.get("retrieval_ok") and "reindex" not in action_ids:
            actions.append({
                "id": "reindex",
                "title": "全量重建索引",
                "why": "修复索引覆盖不足导致的检索失败",
                "auto": True,
            })
            action_ids.add("reindex")
        if "agent-transcripts" in " ".join(h.get("path", "") for h in (it.get("top_hits") or [])) and "rebalance_source_weight" not in action_ids:
            actions.append({
                "id": "rebalance_source_weight",
                "title": "调整检索源权重（降低 transcript）",
                "why": "减少对非结构化对话文本的误依赖",
                "auto": False,
            })
            action_ids.add("rebalance_source_weight")
    if "rerun_eval" not in action_ids:
        actions.append({
            "id": "rerun_eval",
            "title": "重跑评测验证",
            "why": "确认修复是否生效",
            "auto": True,
        })
    checklist.extend([
        "先执行可自动动作（reindex / rerun_eval）",
        "若仍失败，再执行代码级动作（guard/权重）",
        "完成后查看“评测趋势”确认是否提升",
    ])
    return {
        "summary": f"基于 {len(items)} 个失败项生成修复工单",
        "actions": actions,
        "checklist": checklist,
    }


@app.post("/api/eval/execute")
def api_eval_execute(body: EvalActionBody):
    allowed = {"reindex", "rerun_eval"}
    todo = [a for a in body.actions if a in allowed]
    if not todo:
        return {"ok": True, "message": "没有可自动执行的动作", "results": []}
    results: list[dict] = []
    if "reindex" in todo:
        try:
            st = indexer.index(reindex=True)
            results.append({"action": "reindex", "ok": True, "stats": st})
        except Exception as e:
            results.append({"action": "reindex", "ok": False, "error": str(e)})
    if "rerun_eval" in todo:
        run = api_eval_run()
        if isinstance(run, JSONResponse):
            try:
                payload = json.loads(run.body.decode("utf-8"))
            except Exception:
                payload = {"error": "评测执行失败"}
            results.append({"action": "rerun_eval", "ok": False, **payload})
        else:
            results.append({"action": "rerun_eval", "ok": bool(run.get("ok")), **run})
    ok_all = all(r.get("ok") for r in results)
    return {"ok": ok_all, "results": results}


@app.post("/api/eval/run")
def api_eval_run():
    import shutil
    import sys

    script = config.REPO_ROOT / "scripts" / "model_eval.py"
    if not script.exists():
        return JSONResponse({"error": f"评测脚本不存在: {script}"}, status_code=404)
    py = sys.executable
    if not Path(py).exists():
        py = shutil.which("python3") or py
    try:
        proc = subprocess.run(
            [py, str(script)],
            cwd=str(config.REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "评测超时（30分钟）"}, status_code=504)
    except FileNotFoundError:
        return JSONResponse({"error": f"找不到 Python 解释器: {py}"}, status_code=500)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()[-1200:]
        return JSONResponse({"error": f"评测失败: {msg}"}, status_code=500)
    cur = config.LOGS_DIR / "model_eval.json"
    if cur.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        snap = config.LOGS_DIR / f"model_eval-{stamp}.json"
        snap.write_text(cur.read_text(encoding="utf-8"), encoding="utf-8")
    return {"ok": True, "message": "评测完成", "stdout": (proc.stdout or "")[-1200:]}


@app.get("/api/usage")
def api_usage(period: str = "day"):
    start, end = summary._window(period)
    rows, total = usage.stats(start, end)
    return {"total": total, "total_human": usage._fmt(total), "apps": rows}


def run(host: str = "127.0.0.1", port: int = 8765):
    import uvicorn
    db.init_db()
    uvicorn.run(app, host=host, port=port, log_level="warning")
