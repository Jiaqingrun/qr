from __future__ import annotations

import datetime
import json
import logging
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    alerts,
    backfill,
    chat,
    collectors,
    compliance,
    config,
    console_log,
    console_tail,
    context_meter,
    db,
    digest,
    eval_suite,
    export,
    facts,
    prompt_guides,
    governance,
    standards_changelog,
    health,
    indexer,
    models,
    links,
    module_map,
    ops_timeline,
    ops_panel,
    project_brief,
    project_panel,
    project_relations,
    query,
    summary,
    usage,
    workspace,
    timeutil,
)
from .collectors import notes
from .ollama_client import Ollama, OllamaError

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="QR本地知识库")
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
_log = logging.getLogger(__name__)
_db_ready = threading.Event()
_index_lock = threading.Lock()
_index_job: dict[str, object] = {
    "running": False,
    "job_id": None,
    "started_at": None,
    "finished_at": None,
    "stats": None,
    "error": None,
}


def _run_index_background(
    *,
    job_id: str,
    label: str,
    reindex: bool,
    incremental: bool,
    since_days: float | None,
    since_hours: float | None,
) -> None:
    def _progress(path_s: str, n_chunks: int) -> None:
        console_log.job_progress(
            job_id,
            source="web",
            label=label,
            text=f"{path_s} · {n_chunks} 块",
            throttle_key=job_id,
        )

    try:
        stats = indexer.index(
            reindex=reindex,
            incremental=incremental,
            since_days=since_days,
            since_hours=since_hours,
            progress=_progress,
        )
        err = None
        done_text = (
            f"文档 {stats.get('files', 0)} · 向量块 {stats.get('chunks', 0)} · "
            f"跳过 {stats.get('skipped', 0)}"
        )
        console_log.job_done(job_id, source="web", label=label, text=done_text)
    except OllamaError as e:
        stats = None
        err = str(e)
        console_log.job_done(job_id, source="web", label=label, text=err, error=True)
    except sqlite3.OperationalError as e:
        stats = None
        err = (
            "数据库正被其他任务占用（索引/采集/后台同步），请稍候再试。"
            if "locked" in str(e).lower() else str(e)
        )
        console_log.job_done(job_id, source="web", label=label, text=err, error=True)
    except Exception as e:
        stats = None
        err = str(e)
        console_log.job_done(job_id, source="web", label=label, text=err, error=True)
    with _index_lock:
        _index_job["running"] = False
        _index_job["finished_at"] = time.time()
        _index_job["stats"] = stats
        _index_job["error"] = err


def _db_busy_response(exc: sqlite3.OperationalError) -> JSONResponse:
    if "locked" in str(exc).lower():
        return JSONResponse(
            {"error": "数据库正被其他任务占用（索引/采集/后台同步），请稍候再试。"},
            status_code=503,
        )
    return JSONResponse({"error": str(exc)}, status_code=500)


def _ai_power_block() -> JSONResponse | None:
    from . import power_mode

    if power_mode.is_lite():
        return JSONResponse(
            {"error": "AI 服务已关闭。请在侧栏打开「AI 服务」开关后再试。"},
            status_code=503,
        )
    return None


@app.on_event("startup")
def _startup_init_db() -> None:
    def _init() -> None:
        try:
            db.init_db_retry(retries=90, delay=0.5)
            _db_ready.set()
        except Exception as exc:
            _log.error("Web 数据库初始化失败: %s", exc)

    threading.Thread(target=_init, daemon=True).start()
    console_tail.start()


@app.middleware("http")
async def ops_timeline_middleware(request: Request, call_next):
    """将 Web 端写操作实时记入时间线（source=qr）。"""
    method = request.method.upper()
    path = request.url.path
    body_bytes = b""
    if method in ("POST", "PUT", "DELETE", "PATCH") and path.startswith("/api/"):
        body_bytes = await request.body()

        async def receive():
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        request = Request(request.scope, receive)

    response = await call_next(request)

    if method not in ("POST", "PUT", "DELETE", "PATCH") or not path.startswith("/api/"):
        return response
    if ops_timeline.skip_timeline_path(path):
        return response
    if not ops_timeline.should_log_http(method, path, response.status_code):
        return response

    try:
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}

    query = {k: str(v) for k, v in request.query_params.items()}
    described = ops_timeline.describe_http(method, path, payload, query)
    if described:
        action, title, content, project = described
        ops_timeline.log_safe(
            action=action,
            title=title,
            content=content,
            project=project,
            via="web",
            extra={"path": path, "method": method},
        )
    return response


class AskBody(BaseModel):
    question: str
    k: int = 6
    model: str | None = None
    deep: bool = False
    web: bool = False
    session_id: int | None = None
    project: str | None = None
    category: str | None = None
    stream: bool = True
    citations_only: bool = False


class QueryBody(BaseModel):
    text: str
    k: int = 6
    project: str | None = None
    category: str | None = None


class LogBody(BaseModel):
    text: str
    tags: str | None = None
    kind: str = "note"
    project: str | None = None


class SummaryBody(BaseModel):
    period: str = "week"
    date_from: str | None = None
    date_to: str | None = None


class StandardsBody(BaseModel):
    content: str
    note: str = "Web 编辑"


class IndexBody(BaseModel):
    reindex: bool = False
    incremental: bool = False
    since_days: float | None = None
    since_hours: float | None = None


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


class DeleteProjectBody(BaseModel):
    project: str
    confirm_phrase: str
    confirm: str | None = None


class ProjectApproveBody(BaseModel):
    project: str
    approved: bool = True


class ProjectLinkBody(BaseModel):
    from_project: str
    to_project: str
    link_type: str = "related"
    strength: int = 60
    reason: str = ""
    evidence: list[str] | None = None
    pinned: bool = True


class ProjectSuiteBody(BaseModel):
    name: str
    description: str = ""
    role: str = ""
    color: str = ""


class ProjectSuiteUpdateBody(BaseModel):
    name: str | None = None
    description: str | None = None
    role: str | None = None
    color: str | None = None
    sort_order: int | None = None


class ProjectSuiteMembersBody(BaseModel):
    project_ids: list[str]


class ProjectMetaBody(BaseModel):
    project: str
    role: str = ""
    note: str = ""


class ProjectInferBody(BaseModel):
    days: int = 30


class NotifyBody(BaseModel):
    title: str
    body: str = ""


@app.get("/")
def index():
    return FileResponse(
        STATIC / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/status")
def status():
    try:
        db.init_db_retry(retries=5, delay=0.2)
    except Exception:
        pass
    try:
        with db.session() as conn:
            pg = prompt_guides.stats(conn)
            dash = health.status_dashboard(conn)
    except Exception as exc:
        return JSONResponse(
            {
                "error": str(exc),
                "health_ok": False,
                "pillars": [],
                "summary": "状态加载失败",
            },
            status_code=500,
        )
    ollama_tags = dash.get("ollama_tags") or []
    return {
        "events": dash["events"],
        "event_total": dash["event_total"],
        "documents": dash["documents"],
        "chunks": dash["chunks"],
        "summaries": dash["summaries"],
        "chats": dash["chats"],
        "projects": dash["projects"],
        "usage_sessions": dash["usage_sessions"],
        "standards_versions": dash["standards_versions"],
        "prompt_guides": pg,
        "backend": dash["backend"],
        "models": ollama_tags,
        "ollama_ok": dash["ollama_ok"],
        "ollama_models": dash["ollama_models"],
        "pillars": dash["pillars"],
        "summary": dash["summary"],
        "schedule_loaded": dash["schedule_loaded"],
        "schedule_total": dash["schedule_total"],
        "qr_home": str(config.QR_HOME),
        "health_ok": dash["health_ok"],
        "health_issues": dash["health_issues"],
        "health_ok_items": dash["health_ok_items"],
        "default_ask_model": models.default_ask_model(),
        "features": {"prompt_guides": True},
    }


@app.get("/api/ask/models")
def api_ask_models():
    """问答可选模型目录（含 ollama 是否已安装）。"""
    try:
        with Ollama() as ol:
            installed = ol.health()
    except OllamaError:
        installed = []
    return {
        "default": models.default_ask_model(),
        "models": models.list_ask_models_with_status(installed),
    }


class OpenBody(BaseModel):
    path: str
    line: int | None = None
    editor: str | None = None


def _parse_day(s: str) -> datetime.datetime:
    try:
        return timeutil.parse_day(s)
    except ValueError as e:
        raise ValueError("日期格式应为 YYYY-MM-DD") from e


def _day_start(day: datetime.datetime) -> int:
    return timeutil.day_start_local(day)


def _day_end_exclusive(day: datetime.datetime) -> int:
    return timeutil.day_end_exclusive_local(day)


_EVENT_ORDER_ACTIVITY = (
    "CASE source "
    "WHEN 'note' THEN 0 WHEN 'qr' THEN 0 WHEN 'cursor' THEN 1 WHEN 'shell' THEN 2 "
    "WHEN 'git' THEN 3 WHEN 'file' THEN 4 ELSE 5 END, "
    "ts DESC, id DESC"
)


@app.get("/api/events")
def events(
    limit: int = 50,
    page: int = 1,
    source: str | None = None,
    project: str | None = None,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = "time",
    q: str | None = None,
    include_related: bool = False,
):
    limit = max(1, min(limit, 100))
    page = max(1, page)

    if q and q.strip():
        from . import event_links, timeline_search

        try:
            d_from_ts = _day_start(_parse_day(date_from)) if date_from else None
            d_to_ts = _day_end_exclusive(_parse_day(date_to)) if date_to else None
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        with db.session() as conn:
            items = timeline_search.search(
                conn, q.strip(), limit=limit, source=source or None,
                project=project or None,
                date_from_ts=d_from_ts, date_to_ts=d_to_ts,
            )
            out = []
            for it in items:
                row = conn.execute(
                    "SELECT uid, ts, source, project, title, content, meta FROM events WHERE uid=?",
                    (it["uid"],),
                ).fetchone()
                if not row:
                    continue
                if row["source"] == "note" and not notes.is_manual_timeline_note(row["uid"], row["meta"]):
                    continue
                tlabel = timeutil.format_local(row["ts"])
                item = {
                    "uid": row["uid"],
                    "ts": row["ts"],
                    "time": tlabel,
                    "source": row["source"],
                    "project": workspace.sanitize_display_project(row["project"]),
                    "project_label": workspace.project_timeline_label(row["project"]),
                    "title": row["title"],
                    "content": row["content"],
                    "score": it.get("score"),
                }
                link = links.event_link(
                    row["source"], row["title"], row["content"], row["project"],
                    uid=row["uid"], meta=row["meta"],
                )
                if link:
                    item["link"] = link
                if include_related:
                    rel = event_links.related_for_event(
                        conn, uid=row["uid"], source=row["source"],
                        title=row["title"] or "", content=row["content"] or "",
                        meta=row["meta"], ts=int(row["ts"]),
                    )
                    if rel:
                        item["related"] = rel
                out.append(item)
        return {
            "items": out,
            "total": len(out),
            "page": 1,
            "limit": limit,
            "pages": 1,
            "q": q,
            "source": source,
            "sort": sort,
        }

    where: list[str] = []
    args: list = []
    if source:
        where.append("source=?")
        args.append(source)
    if project and project.strip():
        pvals = workspace.project_filter_values(project.strip())
        if pvals:
            ph = ",".join("?" * len(pvals))
            where.append(f"project IN ({ph})")
            args.extend(pvals)

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

    proj_cond, proj_args = workspace.events_project_sql_filter()
    where.append(proj_cond)
    args.extend(proj_args)
    where.append(workspace.events_timeline_hidden_sql())

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    from . import event_links

    out = []
    with db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM events{clause}", args).fetchone()["c"]
        pages = max(1, (total + limit - 1) // limit) if total else 1
        page = min(page, pages)
        offset = (page - 1) * limit
        order = _EVENT_ORDER_ACTIVITY if sort == "activity" else "ts DESC, id DESC"
        rows = conn.execute(
            f"SELECT uid, ts, source, project, title, content, meta FROM events{clause} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
        for r in rows:
            if not workspace.event_row_visible(r["source"], r["project"]):
                continue
            if workspace.event_timeline_hidden(r["source"], r["title"], r["meta"]):
                continue
            if r["source"] == "note" and not notes.is_manual_timeline_note(r["uid"], r["meta"]):
                continue
            ts_estimated = False
            has_reply = False
            if r["meta"]:
                try:
                    meta_obj = json.loads(r["meta"])
                    ts_estimated = bool(meta_obj.get("ts_estimated"))
                    has_reply = bool(meta_obj.get("has_reply"))
                except json.JSONDecodeError:
                    meta_obj = {}
            else:
                meta_obj = {}
            tlabel = timeutil.format_local(r["ts"])
            if ts_estimated:
                tlabel = f"约 {tlabel}"
            item = {
                "uid": r["uid"],
                "ts": r["ts"],
                "time": tlabel,
                "ts_estimated": ts_estimated,
                "source": r["source"],
                "project": workspace.sanitize_display_project(r["project"]),
                "project_label": workspace.project_timeline_label(r["project"]),
                "title": r["title"],
                "content": r["content"],
                "has_reply": has_reply,
            }
            link = links.event_link(
                r["source"],
                r["title"],
                r["content"],
                r["project"],
                uid=r["uid"],
                meta=r["meta"],
            )
            if link:
                item["link"] = link
            if meta_obj.get("prompt_prefix_pending"):
                item["prompt_prefix_pending"] = True
                item["prompt_prefix_hint"] = meta_obj.get(
                    "prompt_prefix_hint",
                    "改为 执行- 主题 可进引导语",
                )
                if meta_obj.get("chat_title"):
                    item["chat_title"] = meta_obj["chat_title"]
            if meta_obj.get("sensitive_warning"):
                item["sensitive_warning"] = True
                item["sensitive_patterns"] = meta_obj.get("sensitive_patterns") or []
                item["sensitive_hint"] = meta_obj.get("sensitive_hint") or ""
            if include_related:
                rel = event_links.related_for_event(
                    conn,
                    uid=r["uid"],
                    source=r["source"],
                    title=r["title"] or "",
                    content=r["content"] or "",
                    meta=r["meta"],
                    ts=int(r["ts"]),
                )
                if rel:
                    item["related"] = rel
            out.append(item)
        from . import session_checkpoint

        session_checkpoint.enrich_timeline_items(conn, out)

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
        "sort": sort,
    }


@app.get("/api/events/{uid}/related")
def event_related(uid: str, limit: int = 8):
    """单条时间线事件的关联项（懒加载，避免列表页 N+1 查询）。"""
    from . import event_links

    with db.session() as conn:
        row = conn.execute(
            "SELECT uid, ts, source, project, title, content, meta FROM events WHERE uid=?",
            (uid,),
        ).fetchone()
        if not row:
            return JSONResponse({"error": "未找到事件"}, status_code=404)
        rel = event_links.related_for_event(
            conn,
            uid=row["uid"],
            source=row["source"],
            title=row["title"] or "",
            content=row["content"] or "",
            meta=row["meta"],
            ts=int(row["ts"]),
            limit=max(1, min(limit, 20)),
        )
    return {"uid": uid, "related": rel}


@app.get("/api/cursor/sessions/long")
def api_cursor_long_sessions(limit: int = 30):
    from . import session_checkpoint

    db.init_db()
    with db.session() as conn:
        sessions = session_checkpoint.list_long_sessions(conn, limit=max(1, min(limit, 100)))
    return {"sessions": sessions, "min_turns": session_checkpoint.min_turns()}


@app.post("/api/cursor/sessions/{session_id}/checkpoint")
def api_cursor_session_checkpoint(session_id: str, force: bool = False):
    from . import session_checkpoint
    from .ollama_client import OllamaError

    db.init_db()
    try:
        with db.session() as conn:
            result = session_checkpoint.create_checkpoint(
                conn, session_id, force=force,
            )
        return {"ok": True, **result}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except OllamaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/open")
def api_open(body: OpenBody):
    try:
        links.open_path(body.path, line=body.line, editor=body.editor)
        return {"ok": True}
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    except subprocess.CalledProcessError as e:
        return JSONResponse({"error": f"打开失败: {e}"}, status_code=500)
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.post("/api/query")
def api_query(body: QueryBody):
    blocked = _ai_power_block()
    if blocked:
        return blocked
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
def api_project_delete(req: DeleteProjectBody):
    try:
        return workspace.purge_project(
            req.project.strip(),
            confirm=(req.confirm or req.project).strip(),
            confirm_phrase=req.confirm_phrase,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


def _finish_ask(
    body: AskBody,
    answer: str,
    hits,
    web_results,
    sid: int,
    *,
    model: str,
):
    def _persist() -> dict:
        with db.write_session(busy_ms=8000) as conn:
            chat.update_session_model(conn, sid, model)
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
                model=model,
                hits=last_hits,
                web_results=last_web,
            )
            return msg_id, session, ctx

    msg_id, session, ctx = db.run_db_retry(_persist)
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
        "citations_only": bool(getattr(body, "citations_only", False)),
        "session": {
            "id": session["id"],
            "title": session["title"],
            "model": session["model"],
            "model_label": session["model_label"],
            "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(session["updated_at"])),
        },
        "model": model,
    }


@app.post("/api/ask")
def api_ask(body: AskBody):
    db.init_db()
    blocked = _ai_power_block()
    if blocked:
        return blocked
    question = body.question.strip()
    if not question:
        return JSONResponse({"error": "问题不能为空"}, status_code=400)

    history = None
    sid = body.session_id
    session_row = None
    try:
        if sid:
            def _load_session() -> tuple[dict | None, list | None]:
                with db.session() as conn:
                    row = chat.get_session(conn, sid)
                    if row is None:
                        return None, None
                    return row, chat.history_for_prompt(conn, sid)

            session_row, history = db.run_db_retry(_load_session)
            if session_row is None:
                return JSONResponse({"error": "对话不存在"}, status_code=404)
        else:
            try:
                resolved = models.resolve_ask_model(body.model, deep_legacy=body.deep)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)

            def _create_session() -> tuple[int, dict]:
                with db.write_session(busy_ms=8000) as conn:
                    new_sid = chat.create_session(
                        conn, title=question, model=resolved, web=body.web,
                    )
                    return new_sid, chat.get_session(conn, new_sid)

            sid, session_row = db.run_db_retry(_create_session)
    except sqlite3.OperationalError as e:
        return _db_busy_response(e)

    try:
        model = models.resolve_ask_model(
            body.model,
            deep_legacy=body.deep,
            session_model=session_row["model"] if session_row else None,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if body.citations_only:
        import json as _json

        def gen_citations():
            try:
                yield f"data: {_json.dumps({'type': 'status', 'text': '正在检索本地出处…'}, ensure_ascii=False)}\n\n"
                answer, hits = query.citations_only(
                    question, body.k, project=body.project, category=body.category,
                )
                yield f"data: {_json.dumps({'type': 'meta', 'hits': hits, 'web': [], 'similar': []}, ensure_ascii=False)}\n\n"
                yield f"data: {_json.dumps({'type': 'token', 'text': answer}, ensure_ascii=False)}\n\n"
                payload = _finish_ask(body, answer, hits, [], sid, model=model)
                yield f"data: {_json.dumps({'type': 'done', **payload}, ensure_ascii=False)}\n\n"
            except OllamaError as e:
                yield f"data: {_json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
            except sqlite3.OperationalError as e:
                msg = (
                    "数据库正被其他任务占用（索引/采集/后台同步），请稍候再试。"
                    if "locked" in str(e).lower() else str(e)
                )
                yield f"data: {_json.dumps({'type': 'error', 'error': msg}, ensure_ascii=False)}\n\n"

        if body.stream:
            return StreamingResponse(
                gen_citations(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        try:
            answer, hits = query.citations_only(
                question, body.k, project=body.project, category=body.category,
            )
        except OllamaError as e:
            return JSONResponse({"error": str(e)}, status_code=502)
        except sqlite3.OperationalError as e:
            return _db_busy_response(e)
        try:
            return _finish_ask(body, answer, hits, [], sid, model=model)
        except sqlite3.OperationalError as e:
            return _db_busy_response(e)

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
                    if ev["type"] in ("meta", "status", "token"):
                        if ev["type"] == "token":
                            answer += ev.get("text", "")
                        elif ev["type"] == "meta":
                            hits = ev.get("hits") or []
                            web_results = ev.get("web") or []
                        yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
                    elif ev["type"] == "done":
                        answer = ev.get("answer") or answer
                        payload = _finish_ask(
                            body, answer, hits, web_results, sid, model=model,
                        )
                        yield f"data: {_json.dumps({'type': 'done', **payload}, ensure_ascii=False)}\n\n"
            except OllamaError as e:
                yield f"data: {_json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
            except sqlite3.OperationalError as e:
                msg = (
                    "数据库正被其他任务占用（索引/采集/后台同步），请稍候再试。"
                    if "locked" in str(e).lower() else str(e)
                )
                yield f"data: {_json.dumps({'type': 'error', 'error': msg}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        answer, hits, web_results = query.ask(
            question, body.k, model=model, web=body.web,
            history=history, project=body.project, category=body.category,
        )
    except OllamaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    except sqlite3.OperationalError as e:
        return _db_busy_response(e)
    try:
        return _finish_ask(body, answer, hits, web_results, sid, model=model)
    except sqlite3.OperationalError as e:
        return _db_busy_response(e)


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
            model=session["model"],
            hits=last_hits,
            web_results=last_web,
        )

    return {
        "id": session["id"],
        "title": session["title"],
        "deep": session["deep"],
        "web": session["web"],
        "model": session["model"],
        "model_label": session["model_label"],
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
    model: str | None = None,
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
    use_web = session["web"] if web is None else web
    try:
        use_model = models.resolve_ask_model(
            model,
            deep_legacy=bool(deep) if deep is not None else False,
            session_model=session["model"],
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    ctx = context_meter.estimate_ask_context(
        history=history,
        question=question.strip(),
        k=max(1, min(k, 20)),
        web=use_web,
        model=use_model,
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
    kind = (body.kind or "note").strip().lower()
    if kind not in ("note", "decision", "activity"):
        return JSONResponse({"error": f"不支持的 kind: {body.kind}"}, status_code=400)
    text = body.text
    if kind == "activity" and text and not text.strip().startswith("[活动]"):
        text = f"[活动] {text.strip()}"
    with db.session() as conn:
        r = notes.add_note(
            conn,
            text,
            tags=body.tags,
            kind=kind,
            project=body.project,
        )
        if r == "cursor_echo":
            return {
                "ok": False,
                "skipped": True,
                "reason": "与 Cursor 对话重复，未写入笔记时间线（请查看来源 cursor）",
            }
    return {"ok": True}


@app.post("/api/ingest")
def api_ingest():
    db.init_db()
    jid = console_log.job_start(source="web", label="行为采集")
    try:
        with db.session() as conn:
            res = collectors.run(conn, ["shell", "git", "files", "cursor", "notes"])
            deduped = notes.purge_cursor_duplicate_notes(conn)
        total = sum(v for v in res.values() if isinstance(v, int))
        console_log.job_done(
            jid, source="web", label="行为采集",
            text=f"采集 {total} 条 · 去重笔记 {deduped}",
        )
        return {"ingested": res, "notes_deduped": deduped, "job_id": jid}
    except sqlite3.OperationalError as e:
        console_log.job_done(jid, source="web", label="行为采集", text=str(e), error=True)
        return _db_busy_response(e)


@app.post("/api/backfill")
def api_backfill(days: int = 365):
    db.init_db()
    jid = console_log.job_start(source="web", label=f"全量补录（近 {days} 天）")
    with db.session() as conn:
        res = backfill.run(conn, days=days)
    total = sum(v for k, v in res.items() if isinstance(v, int))
    console_log.job_done(
        jid, source="web", label=f"全量补录（近 {days} 天）",
        text=f"共 {total} 条",
    )
    return {"result": res, "total": total, "job_id": jid}


@app.post("/api/ingest/cursor")
def api_ingest_cursor(backfill: bool = False, days: int = 365):
    db.init_db()
    try:
        with db.session() as conn:
            if backfill:
                res = backfill.run(conn, days=days, sources=["cursor"])
                n = res.get("cursor", 0)
            else:
                from .collectors import cursor as cursor_col
                n = cursor_col.collect(conn)
        return {"ingested": n, "backfill": backfill, "days": days if backfill else None}
    except sqlite3.OperationalError as e:
        return _db_busy_response(e)


@app.post("/api/index")
def api_index(
    background_tasks: BackgroundTasks,
    body: IndexBody | None = None,
):
    db.init_db()
    req = body or IndexBody()
    with _index_lock:
        if _index_job.get("running"):
            return JSONResponse(
                {"error": "索引正在进行中，请稍候完成后再试"},
                status_code=409,
            )
        mode = "全量重建" if req.reindex else (
            "增量" if req.incremental or req.since_days or req.since_hours else "常规"
        )
        job_id = console_log.new_job_id("index")
        label = f"索引 · {mode}"
        console_log.job_start(source="web", label=label, job_id=job_id)
        _index_job.update({
            "running": True,
            "job_id": job_id,
            "started_at": time.time(),
            "finished_at": None,
            "stats": None,
            "error": None,
        })
    background_tasks.add_task(
        _run_index_background,
        job_id=job_id,
        label=label,
        reindex=req.reindex,
        incremental=req.incremental,
        since_days=req.since_days,
        since_hours=req.since_hours,
    )
    return {"started": True, "running": True, "job_id": job_id}


@app.get("/api/index/status")
def api_index_status():
    with _index_lock:
        return dict(_index_job)


@app.get("/api/resume")
def api_resume():
    """接着干：开工总览卡片。"""
    from . import resume_panel

    db.init_db()
    with db.session() as conn:
        return resume_panel.generate(conn)


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
    from_conversations: bool = False
    confirm: bool | None = None


class ConfirmStandardsBody(BaseModel):
    note: str = ""


class ProjectStandardsBody(BaseModel):
    content: str
    note: str = "Web 编辑"


class ProjectReviseBody(BaseModel):
    period: str = "week"


class ActivateStandardsBody(BaseModel):
    version_id: int
    note: str | None = None


@app.get("/api/standards")
def api_standards():
    governance.ensure_standards()
    return {"content": governance.read_standards(), "versions": governance.list_versions()}


@app.get("/api/standards/changelog")
def api_standards_changelog(prune: bool = False):
    """只读规范沿革；清理请用 POST /api/standards/changelog/prune。"""
    governance.ensure_standards()
    return standards_changelog.build_changelog(prune_identical=prune)


@app.get("/api/standards/history")
def api_standards_history(prune: bool = False):
    """规范沿革（与 /api/standards/changelog 相同，推荐新代码使用本路径）。"""
    return api_standards_changelog(prune=prune)


@app.post("/api/standards/changelog/prune")
def api_standards_changelog_prune():
    """删除无效归档（测试备注、与上一版相同、无可展示 diff），并返回最新沿革。"""
    governance.ensure_standards()
    noise = governance.prune_noise_versions()
    stats = governance.prune_redundant_versions()
    changelog = standards_changelog.build_changelog(prune_identical=False)
    return {**changelog, **stats, "pruned_noise": noise}


@app.get("/api/standards/version/{vid}")
def api_standards_version(vid: int):
    content = governance.get_version(vid)
    if content is None:
        return JSONResponse({"error": "版本不存在"}, status_code=404)
    return {"content": content}


@app.get("/api/standards/pending")
def api_standards_pending():
    from . import standards_revision

    governance.ensure_standards()
    pending = standards_revision.load_pending()
    if not pending:
        return {"pending": False}
    return {
        "pending": True,
        "note": pending.get("note"),
        "created_at": pending.get("created_at"),
        "period": pending.get("period"),
        "from_conversations": pending.get("from_conversations"),
        "diff": pending.get("diff"),
        "preview": pending.get("after"),
    }


@app.post("/api/standards/confirm")
def api_standards_confirm(body: ConfirmStandardsBody):
    from . import standards_revision

    governance.ensure_standards()
    try:
        content, version_saved = standards_revision.confirm_pending(note=body.note)
        governance.generate_rules_all_workspace()
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {
        "ok": True,
        "content": content,
        "version_saved": version_saved,
        "versions": governance.list_versions(),
    }


@app.post("/api/standards/reject")
def api_standards_reject():
    from . import standards_revision

    standards_revision.reject_pending()
    return {"ok": True}


@app.post("/api/standards/revise")
def api_standards_revise(body: ReviseBody):
    try:
        if body.from_conversations:
            content, version_saved, content_changed, pending = governance.revise_from_conversations(
                body.period, confirm=body.confirm
            )
        else:
            content, version_saved, content_changed, pending = governance.revise_from_behavior(
                body.period, confirm=body.confirm
            )
        from . import standards_revision

        payload: dict = {
            "content": governance.read_standards() if pending else content,
            "preview": content if pending else None,
            "version_saved": version_saved,
            "content_changed": content_changed,
            "pending": pending,
            "versions": governance.list_versions(),
        }
        if pending:
            pend = standards_revision.load_pending()
            if pend:
                payload["diff"] = pend.get("diff")
                payload["pending_note"] = pend.get("note")
        return payload
    except OllamaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/project/standards")
def api_project_standards_get(project: str):
    from . import project_standards, workspace

    pid = workspace.normalize_project_id(project)
    proj_dir = workspace.resolve_project_dir(pid)
    if not proj_dir:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    project_standards.ensure_project_standards(proj_dir, project_id=pid)
    body = project_standards.read_project_standards(proj_dir) or ""
    return {
        "project": pid,
        "content": body,
        "path": str((proj_dir / project_standards.PROJECT_MD).resolve()),
        "versions": project_standards.list_project_versions(pid),
    }


@app.put("/api/project/standards")
def api_project_standards_put(project: str, body: ProjectStandardsBody):
    from . import project_standards, workspace

    pid = workspace.normalize_project_id(project)
    proj_dir = workspace.resolve_project_dir(pid)
    if not proj_dir:
        return JSONResponse({"error": "项目不存在"}, status_code=404)
    try:
        version_saved = project_standards.save_project_standards(
            proj_dir, body.content, project_id=pid, note=body.note
        )
        governance.generate_rules(proj_dir)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {
        "ok": True,
        "project": pid,
        "version_saved": version_saved,
        "versions": project_standards.list_project_versions(pid),
    }


@app.post("/api/project/standards/revise")
def api_project_standards_revise(project: str, body: ProjectReviseBody):
    from . import project_standards

    try:
        content, version_saved = project_standards.revise_from_conversations(
            project, body.period
        )
        return {
            "content": content,
            "version_saved": version_saved,
            "project": workspace.normalize_project_id(project),
        }
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
    version_saved = governance.save_standards(body.content, note=body.note)
    return {
        "ok": True,
        "version_saved": version_saved,
        "versions": governance.list_versions(),
    }


@app.get("/api/digest")
def api_digest(days: int = 1):
    return digest.generate(days=max(1, min(days, 30)))


@app.post("/api/digest/notify")
def api_digest_notify(days: int = 1):
    return alerts.publish_digest(days=max(1, min(days, 30)), notify=True)


@app.post("/api/notify")
def api_notify(body: NotifyBody):
    """macOS 系统通知（总结 / 规范 / 洞察等任务完成时由 Web 调用）。"""
    ok = alerts.notify(body.title, body.body)
    return {"notified": ok}


def _project_brief_response(project: str = "", *, auto: bool = True):
    if project.strip():
        pid = workspace.normalize_project_id(project.strip())
        return project_brief.brief(pid, prefer_detected=False)
    if auto:
        return project_brief.brief("", prefer_detected=True)
    return JSONResponse({"error": "缺少 project 参数"}, status_code=400)


@app.get("/api/project/brief")
def api_project_brief(project: str = "", auto: bool = True):
    """项目简介（用途 / 功能点 / 完成度）。"""
    return _project_brief_response(project, auto=auto)


@app.get("/api/project")
def api_project_panel(
    project: str = "",
    days: int = 14,
    focus: bool = False,
    auto: bool = True,
):
    """focus=1：项目简介；否则为项目体检面板。"""
    if focus:
        return _project_brief_response(project, auto=auto)
    if not project.strip():
        return JSONResponse({"error": "缺少 project 参数"}, status_code=400)
    pid = workspace.normalize_project_id(project.strip())
    return project_panel.panel(pid, days=max(1, min(days, 90)))


@app.get("/api/project/focus")
def api_project_focus(project: str | None = None, auto: bool = True):
    """已废弃：请用 /api/project/brief。"""
    body = _project_brief_response(project or "", auto=auto)
    dep = {"Deprecation": "true", "Link": '</api/project/brief>; rel="successor-version"'}
    if isinstance(body, JSONResponse):
        body.headers.update(dep)
        return body
    return JSONResponse(content=body, headers=dep)


@app.post("/api/project/approve")
def api_project_approve(req: ProjectApproveBody):
    if not req.project.strip():
        return JSONResponse({"error": "缺少 project 参数"}, status_code=400)
    return project_brief.set_completion_approved(req.project, req.approved)


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


class PromptTypeBody(BaseModel):
    name: str
    description: str = ""


class PromptMergeBody(BaseModel):
    fragment_ids: list[int]
    title: str | None = None
    type_id: int | None = None
    type_name: str | None = None
    project: str | None = None
    tags: list[str] = []


class PromptGuideBody(BaseModel):
    title: str
    body: str
    type_id: int | None = None
    type_name: str | None = None
    project: str | None = None
    tags: list[str] = []


class PromptFragmentTypeBody(BaseModel):
    type_id: int | None = None
    type_name: str | None = None


class PromptFragmentsDeleteBody(BaseModel):
    fragment_ids: list[int]


class PromptSessionsDeleteBody(BaseModel):
    session_ids: list[str]


class PromptRefineGenerateBody(BaseModel):
    limit: int = 5
    include_inbox: bool = True
    include_raw_guides: bool = True
    model: str | None = None


class PromptRefineApproveBody(BaseModel):
    title: str | None = None
    body: str | None = None


@app.get("/api/prompts/stats")
def api_prompts_stats():
    db.init_db()
    with db.session() as conn:
        return prompt_guides.stats(conn)


@app.get("/api/prompts/types")
def api_prompts_types():
    db.init_db()
    with db.session() as conn:
        return {"types": prompt_guides.list_types(conn)}


@app.post("/api/prompts/types")
def api_prompts_types_add(body: PromptTypeBody):
    db.init_db()
    with db.session() as conn:
        tid = prompt_guides.get_or_create_type(
            conn, body.name, description=body.description,
        )
        conn.commit()
        types = prompt_guides.list_types(conn)
        row = next((t for t in types if t["id"] == tid), None)
        return {"ok": True, "type": row}


@app.get("/api/prompts/fragments")
def api_prompts_fragments(
    type_id: int | None = None,
    project: str | None = None,
    limit: int = 200,
):
    db.init_db()
    with db.session() as conn:
        return {
            "fragments": prompt_guides.list_fragments(
                conn, inbox_only=True, type_id=type_id, project=project or None,
                limit=max(1, min(limit, 500)),
            ),
        }


@app.get("/api/prompts/inbox-groups")
def api_prompts_inbox_groups(
    type_id: int | None = None,
    project: str | None = None,
    sessions: str = "",
    limit: int = 500,
):
    db.init_db()
    session_ids = [s.strip() for s in sessions.split(",") if s.strip()] or None
    with db.session() as conn:
        return prompt_guides.list_inbox_groups(
            conn,
            type_id=type_id,
            project=project or None,
            session_ids=session_ids,
            limit=max(1, min(limit, 800)),
        )


@app.post("/api/prompts/repair-times")
def api_prompts_repair_times():
    db.init_db()
    with db.session() as conn:
        repair = prompt_guides.repair_inbox_timestamps(conn)
        return {"ok": True, **repair}


@app.post("/api/prompts/sync")
def api_prompts_sync():
    db.init_db()
    with db.session() as conn:
        sync = prompt_guides.sync_cursor_inbox(conn)
        from .collectors import notes as notes_col

        notes_n = notes_col.collect(conn)
        conn.commit()
        return {"ok": True, "sync": sync, "notes": notes_n, "repair": sync.get("repair")}


@app.post("/api/prompts/reclassify")
def api_prompts_reclassify():
    db.init_db()
    with db.session() as conn:
        n = prompt_guides.reclassify_inbox_auto(conn)
        return {"ok": True, "updated": n}


@app.post("/api/prompts/fragments/delete")
def api_prompts_fragments_delete(body: PromptFragmentsDeleteBody):
    if not body.fragment_ids:
        return JSONResponse({"error": "fragment_ids 为空"}, status_code=400)
    db.init_db()
    try:
        with db.write_session() as conn:
            result = prompt_guides.delete_fragments(conn, body.fragment_ids)
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            return JSONResponse({"error": "数据库繁忙，请稍后重试"}, status_code=503)
        raise
    return {"ok": True, **result}


@app.post("/api/prompts/sessions/delete")
def api_prompts_sessions_delete(body: PromptSessionsDeleteBody):
    if not body.session_ids:
        return JSONResponse({"error": "session_ids 为空"}, status_code=400)
    db.init_db()
    try:
        with db.write_session() as conn:
            result = prompt_guides.delete_cursor_sessions(conn, body.session_ids)
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            return JSONResponse({"error": "数据库繁忙，请稍后重试"}, status_code=503)
        raise
    return {"ok": True, **result}


@app.patch("/api/prompts/fragments/{fid}")
def api_prompts_fragment_type(fid: int, body: PromptFragmentTypeBody):
    db.init_db()
    with db.session() as conn:
        prompt_guides.update_fragment_type(
            conn, fid, type_id=body.type_id, type_name=body.type_name,
        )
        return {"ok": True}


@app.get("/api/prompts/guides")
def api_prompts_guides(
    type_id: int | None = None,
    origin: str | None = None,
    project: str | None = None,
):
    db.init_db()
    with db.session() as conn:
        return {
            "guides": prompt_guides.list_guides(
                conn,
                type_id=type_id,
                origin=origin or None,
                project=project or None,
            ),
        }


@app.get("/api/prompts/guides/{gid}")
def api_prompts_guide(gid: int):
    db.init_db()
    with db.session() as conn:
        g = prompt_guides.get_guide(conn, gid)
        if not g:
            return JSONResponse({"error": "未找到"}, status_code=404)
        return g


@app.post("/api/prompts/merge")
def api_prompts_merge(body: PromptMergeBody):
    db.init_db()
    with db.session() as conn:
        g = prompt_guides.merge_fragments(
            conn,
            body.fragment_ids,
            title=body.title,
            type_id=body.type_id,
            type_name=body.type_name,
            project=body.project,
            tags=body.tags,
        )
        return {"ok": True, "guide": g}


@app.post("/api/prompts/guides")
def api_prompts_guide_create(body: PromptGuideBody):
    db.init_db()
    with db.session() as conn:
        g = prompt_guides.create_guide_manual(
            conn,
            body.title,
            body.body,
            type_id=body.type_id,
            type_name=body.type_name,
            project=body.project,
            tags=body.tags,
        )
        return {"ok": True, "guide": g}


@app.delete("/api/prompts/guides/{gid}")
def api_prompts_guide_delete(gid: int):
    db.init_db()
    with db.session() as conn:
        prompt_guides.delete_guide(conn, gid)
        return {"ok": True}


@app.get("/api/prompts/refine/candidates")
def api_prompts_refine_candidates(
    include_inbox: bool = True,
    include_raw_guides: bool = True,
):
    from . import prompt_refine

    db.init_db()
    with db.session() as conn:
        items = prompt_refine.list_candidates(
            conn,
            include_inbox=include_inbox,
            include_raw_guides=include_raw_guides,
        )
        pending = prompt_refine.list_proposals(conn, status=prompt_refine.STATUS_PENDING)
        return {"candidates": items, "pending_count": len(pending)}


@app.get("/api/prompts/refine/proposals")
def api_prompts_refine_proposals(status: str = "pending"):
    from . import prompt_refine

    db.init_db()
    with db.session() as conn:
        return {
            "proposals": prompt_refine.list_proposals(conn, status=status),
        }


@app.post("/api/prompts/refine/generate")
def api_prompts_refine_generate(body: PromptRefineGenerateBody):
    from . import prompt_refine
    from .ollama_client import OllamaError

    db.init_db()
    try:
        with db.session() as conn:
            result = prompt_refine.generate_proposals(
                conn,
                limit=max(1, min(body.limit, 20)),
                include_inbox=body.include_inbox,
                include_raw_guides=body.include_raw_guides,
                model=body.model,
            )
        return {"ok": True, **result}
    except OllamaError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/prompts/refine/proposals/{pid}/approve")
def api_prompts_refine_approve(pid: int, body: PromptRefineApproveBody):
    from . import prompt_refine

    db.init_db()
    try:
        with db.session() as conn:
            result = prompt_refine.approve_proposal(
                conn, pid, title=body.title, body=body.body,
            )
        return {"ok": True, **result}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/prompts/refine/proposals/{pid}/reject")
def api_prompts_refine_reject(pid: int):
    from . import prompt_refine

    db.init_db()
    try:
        with db.session() as conn:
            prompt_refine.reject_proposal(conn, pid)
        return {"ok": True}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


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


class DecisionDraftBody(BaseModel):
    project: str = ""
    session: str = ""
    turns: int = 30


@app.post("/api/decision/draft")
def api_decision_draft(body: DecisionDraftBody):
    from . import decision_draft

    db.init_db()
    return decision_draft.build_draft(
        session_id=body.session.strip() or None,
        project=body.project.strip() or None,
        turn_limit=max(5, min(body.turns, 80)),
    )


@app.get("/api/ship-check")
def api_ship_check(project: str = ""):
    from . import ship_check

    db.init_db()
    return ship_check.run_ship_check(
        project=project.strip() or None,
        skip_tests=True,
    )


@app.get("/api/ai-assess/snapshot")
def api_ai_assess_snapshot():
    from . import ai_assess

    db.init_db()
    return ai_assess.collect_snapshot()


@app.get("/api/workspace/cursor-alignment")
def api_workspace_cursor_alignment():
    return health.cursor_alignment_for_web()


@app.get("/api/prompts/suggest-merge")
def api_prompts_suggest_merge(threshold: float = 0.72, limit: int = 200):
    db.init_db()
    with db.session() as conn:
        clusters = prompt_guides.suggest_merge_clusters(
            conn,
            threshold=max(0.5, min(threshold, 0.95)),
            limit=max(20, min(limit, 500)),
        )
    return {"clusters": clusters, "count": len(clusters)}


@app.get("/api/compliance")
def api_compliance(ship: bool = False, days: int = 0):
    if ship:
        db.init_db()
        with db.session() as conn:
            return compliance.scan_ship_checks(
                conn, days=days if days > 0 else None,
            )
    return {"results": compliance.scan_index_roots()}


@app.get("/api/graph")
def api_graph(limit: int = 40):
    return compliance.knowledge_graph(limit=max(10, min(limit, 100)))


@app.get("/api/projects/relations")
def api_projects_relations(suite_id: int | None = None):
    return project_relations.build_graph(suite_id=suite_id)


@app.post("/api/projects/relations/infer")
def api_projects_relations_infer(req: ProjectInferBody):
    days = max(7, min(req.days, 180))
    return project_relations.infer_and_store(days=days)


@app.post("/api/projects/relations/links")
def api_projects_relations_link(req: ProjectLinkBody):
    try:
        return project_relations.upsert_link(
            from_project=req.from_project,
            to_project=req.to_project,
            link_type=req.link_type,
            strength=req.strength,
            reason=req.reason,
            evidence=req.evidence,
            pinned=req.pinned,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/projects/relations/links/{link_id}")
def api_projects_relations_link_delete(link_id: int):
    ok = project_relations.delete_link(link_id)
    return {"ok": ok}


@app.post("/api/projects/relations/suites")
def api_projects_relations_suite_create(req: ProjectSuiteBody):
    return project_relations.create_suite(
        name=req.name,
        description=req.description,
        role=req.role,
        color=req.color,
    )


@app.put("/api/projects/relations/suites/{suite_id}")
def api_projects_relations_suite_update(suite_id: int, req: ProjectSuiteUpdateBody):
    ok = project_relations.update_suite(
        suite_id,
        name=req.name,
        description=req.description,
        role=req.role,
        color=req.color,
        sort_order=req.sort_order,
    )
    return {"ok": ok}


@app.delete("/api/projects/relations/suites/{suite_id}")
def api_projects_relations_suite_delete(suite_id: int):
    return {"ok": project_relations.delete_suite(suite_id)}


@app.put("/api/projects/relations/suites/{suite_id}/members")
def api_projects_relations_suite_members(suite_id: int, req: ProjectSuiteMembersBody):
    n = project_relations.set_suite_members(suite_id, req.project_ids)
    return {"ok": True, "count": n}


@app.post("/api/projects/relations/suites/{suite_id}/members")
def api_projects_relations_suite_member_add(suite_id: int, req: ProjectMetaBody):
    project_relations.add_suite_member(suite_id, req.project, note=req.note)
    return {"ok": True}


@app.delete("/api/projects/relations/suites/{suite_id}/members/{project_id:path}")
def api_projects_relations_suite_member_remove(suite_id: int, project_id: str):
    return {"ok": project_relations.remove_suite_member(suite_id, project_id)}


@app.put("/api/projects/relations/meta")
def api_projects_relations_meta(req: ProjectMetaBody):
    project_relations.set_project_meta(req.project, role=req.role, note=req.note)
    return {"ok": True}


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
        scores = eval_suite.model_pass_counts(data)
        total = eval_suite.eval_case_total(data)
        items.append({
            "file": p.name,
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
            "scores": scores,
            "qwen_ok": scores.get("qwen", 0),
            "deep_ok": scores.get("deepseek", 0),
            "total": total,
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
    if "bge-m3" in ap or "nomic" in ap or "默认" in ap:
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
    for model_key, model_results in (data.get("results") or {}).items():
        if not isinstance(model_results, list):
            continue
        for r in model_results:
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
    from . import eval_runner

    result = eval_runner.run_model_eval()
    if not result.get("ok"):
        return JSONResponse({"error": result.get("error", "评测失败")}, status_code=500)
    return {
        "ok": True,
        "message": "评测完成",
        "path": result.get("path"),
        "snapshot": result.get("snapshot"),
        "markdown": result.get("markdown"),
        "stdout": result.get("stdout"),
    }


@app.get("/api/usage")
def api_usage(period: str = "day"):
    start, end = summary._window(period)
    rows, total = usage.stats(start, end)
    return {"total": total, "total_human": usage._fmt(total), "apps": rows}


class OpsImportBody(BaseModel):
    paths: list[str]
    move: bool = False


class OpsScheduleUninstallBody(BaseModel):
    label: str


@app.get("/api/ops/overview")
def api_ops_overview():
    db.init_db()
    with db.session() as conn:
        return ops_panel.overview(conn)


@app.get("/api/module-map")
def api_module_map():
    return module_map.module_map()


class TrackerPauseBody(BaseModel):
    duration: str = "2h"
    resume: bool = False


@app.get("/api/tracker/status")
def api_tracker_status():
    from . import tracker as tr

    cfg = config.load_config()
    st = tr.pause_status()
    return {
        **st,
        "exclude_apps": list(cfg.get("tracker_exclude_apps") or []),
        "exclude_bundles": list(cfg.get("tracker_exclude_bundles") or []),
    }


@app.post("/api/tracker/pause")
def api_tracker_pause(body: TrackerPauseBody):
    from . import tracker as tr

    try:
        spec = "off" if body.resume else (body.duration or "2h")
        result = tr.set_pause(spec)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True, **result}


class PowerBody(BaseModel):
    enabled: bool | None = None


@app.get("/api/power/status")
def api_power_status():
    from . import power_mode

    return {"ok": True, **power_mode.status()}


@app.post("/api/power")
def api_power_set(body: PowerBody | None = None):
    from . import power_mode

    payload = body or PowerBody()
    if payload.enabled is None:
        result = power_mode.toggle()
    else:
        result = power_mode.set_enabled(payload.enabled)
    ops_timeline.log_safe(
        action="power.mode",
        title="[知识库] AI 服务",
        content=result.get("message") or result.get("hint") or result.get("mode", ""),
    )
    return {"ok": True, **result}


@app.get("/api/shell/stats")
def api_shell_stats(days: int = 7):
    from . import shell_check

    return shell_check.timestamp_stats(days=max(1, min(days, 90)))


@app.post("/api/ops/backup/verify")
def api_ops_backup_verify(body: dict | None = None):
    from . import backup_ops

    path = (body or {}).get("path", "")
    if not path:
        latest = backup_ops.latest_backup_info()
        path = latest.get("path") or ""
    if not path:
        return JSONResponse({"error": "尚无备份可校验"}, status_code=400)
    result = backup_ops.verify_backup(path)
    return {"ok": bool(result.get("ok")), **result}


@app.post("/api/ops/backup")
def api_ops_backup():
    db.init_db()
    jid = console_log.job_start(source="web", label="数据库备份")
    try:
        result = ops_panel.run_backup()
    except OSError as e:
        console_log.job_done(jid, source="web", label="数据库备份", text=str(e), error=True)
        return JSONResponse({"error": str(e)}, status_code=500)
    console_log.job_done(
        jid, source="web", label="数据库备份", text=result.get("path", ""),
    )
    ops_timeline.log_safe(
        action="ops.backup",
        title="[知识库] 数据库备份",
        content=result["path"],
    )
    return {"ok": True, **result, "job_id": jid}


@app.post("/api/ops/backup/restore")
def api_ops_backup_restore(body: dict):
    path = (body or {}).get("path", "")
    if not path:
        return JSONResponse({"error": "缺少 path"}, status_code=400)
    try:
        result = ops_panel.restore_backup(path)
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    ops_timeline.log_safe(
        action="ops.backup_restore",
        title="[知识库] 恢复数据库备份",
        content=result.get("restored_from", ""),
    )
    return result


@app.post("/api/ops/index-health")
def api_ops_index_health(body: dict | None = None):
    cleanup = bool((body or {}).get("cleanup"))
    label = "索引健康" + (" · 清理孤儿" if cleanup else "")
    jid = console_log.job_start(source="web", label=label)
    rep = ops_panel.index_health(cleanup=cleanup)
    orphans = (rep.get("cleanup") or {}).get("documents_removed", 0) if cleanup else 0
    text = f"文档 {rep.get('documents', '?')} · 缺失源文件 {rep.get('missing_files', '?')}"
    if cleanup:
        text += f" · 已清理 {orphans}"
    console_log.job_done(jid, source="web", label=label, text=text)
    return {**rep, "job_id": jid}


@app.get("/api/changelog")
def api_changelog(project: str, days: int = 7):
    from . import changelog

    pid = workspace.normalize_project_id(project.strip())
    return changelog.generate(pid, days=max(1, min(days, 90)))


@app.get("/api/changelog/{project:path}")
def api_changelog_path(project: str, days: int = 7):
    """兼容路径形式 /api/changelog/dev/qr。"""
    return api_changelog(project=project, days=days)


@app.get("/api/alerts")
def api_alerts():
    from . import proactive

    items = proactive.collect_all()
    return {"alerts": items}


@app.get("/api/today")
def api_today():
    from . import today_panel

    db.init_db()
    with db.session() as conn:
        return today_panel.generate(conn)


class FocusProjectBody(BaseModel):
    project: str = ""


@app.get("/api/focus-project")
def api_focus_project_get():
    cfg = config.load_config()
    raw = (cfg.get("focus_project") or "").strip()
    pid = workspace.canonical_project_id(raw, cfg) if raw else ""
    return {
        "focus_project": pid or raw or None,
        "projects": query.workspace_list_projects().get("projects", []),
    }


@app.post("/api/focus-project")
def api_focus_project_set(body: FocusProjectBody):
    cfg = config.load_config()
    raw = (body.project or "").strip()
    if raw:
        pid = workspace.canonical_project_id(raw, cfg) or workspace.normalize_project_id(raw)
        if not workspace.is_listable_project_id(pid):
            return JSONResponse({"error": f"未知项目: {raw}"}, status_code=400)
        cfg["focus_project"] = pid
    else:
        cfg["focus_project"] = ""
    config.save_config(cfg)
    return {"focus_project": cfg.get("focus_project") or None, "ok": True}


class DailyPlanToggleBody(BaseModel):
    id: str
    done: bool
    date: str | None = None


@app.get("/api/insight/daily-plan")
def api_insight_daily_plan(date: str | None = None):
    from . import daily_plan

    return daily_plan.list_for_date(date)


@app.post("/api/insight/daily-plan/toggle")
def api_insight_daily_plan_toggle(body: DailyPlanToggleBody):
    from . import daily_plan

    try:
        return daily_plan.set_done(body.id, body.done, day=body.date)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/symbol")
def api_symbol(name: str, project: str | None = None, limit: int = 20):
    from . import symbol_index

    db.init_db()
    pid = workspace.normalize_project_id(project) if project and project.strip() else None
    hits = symbol_index.search(name, project=pid, limit=max(1, min(limit, 50)))
    return {"hits": hits, "stats": symbol_index.stats()}


@app.post("/api/ops/git-sync-roots")
def api_ops_git_sync_roots():
    result = ops_panel.sync_git_scan_roots()
    return {"ok": True, **result}


@app.get("/api/ops/import/discover")
def api_ops_import_discover():
    return {"projects": ops_panel.discover_imports()}


@app.post("/api/ops/import")
def api_ops_import(body: OpsImportBody):
    result = ops_panel.import_paths(body.paths, move=body.move)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@app.post("/api/ops/schedule/uninstall")
def api_ops_schedule_uninstall(body: OpsScheduleUninstallBody):
    result = ops_panel.uninstall_schedule_agent(body.label)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    ops_timeline.log_safe(
        action="ops.schedule.uninstall",
        title="[知识库] 卸载定时任务",
        content=f"{result.get('title') or body.label} ({body.label})",
    )
    return result


@app.post("/api/ops/schedule/install")
def api_ops_schedule_install(background_tasks: BackgroundTasks):
    result = ops_panel.install_schedule(include_web=False)
    if not result.get("ok"):
        return JSONResponse(result, status_code=500)
    background_tasks.add_task(_ops_install_web_agents_background)
    result["web_restart_pending"] = True
    result["message"] = "采集与同步任务已安装；Web 服务将在数秒后自动重装"
    return result


def _ops_install_web_agents_background() -> None:
    from . import schedule_service

    try:
        schedule_service.install_web_agents()
    except Exception:
        logging.exception("后台安装 Web launchd 任务失败")


@app.post("/api/ops/optimize")
def api_ops_optimize():
    from . import optimize as opt

    db.init_db()
    jid = console_log.job_start(source="web", label="一键优化")
    before = opt.metrics_snapshot()
    try:
        result = opt.run(
            reindex=True,
            run_summary=True,
            run_standards_auto=True,
            merge_prompts=True,
        )
    except OllamaError as e:
        console_log.job_done(jid, source="web", label="一键优化", text=str(e), error=True)
        return JSONResponse({"error": str(e)}, status_code=502)
    except Exception as e:
        console_log.job_done(jid, source="web", label="一键优化", text=str(e), error=True)
        return JSONResponse({"error": str(e)}, status_code=500)
    steps = ", ".join(result.get("steps") or [])
    console_log.job_done(jid, source="web", label="一键优化", text=steps or "完成")
    return {"ok": True, "before": before, "after": result["after"], "steps": result["steps"], "job_id": jid}


@app.get("/api/console/events")
def api_console_events(
    since: int = 0,
    limit: int = 200,
    source: str = "",
    agent: str = "",
):
    events = console_log.tail(
        since_ts=since,
        limit=limit,
        source=source.strip(),
        agent=agent.strip(),
    )
    return {"events": events}


@app.get("/api/console/jobs")
def api_console_jobs():
    jobs = console_log.active_jobs()
    with _index_lock:
        if _index_job.get("running") and _index_job.get("job_id"):
            jid = str(_index_job["job_id"])
            if not any(j.get("job_id") == jid for j in jobs):
                jobs.append({
                    "job_id": jid,
                    "label": "索引",
                    "source": "web",
                    "started_at": _index_job.get("started_at"),
                })
    return {"jobs": jobs}


@app.get("/api/console/agents")
def api_console_agents():
    return {"agents": console_log.agent_log_files()}


@app.get("/api/console/stream")
def api_console_stream(since: int = 0):
    import json as _json

    def gen():
        if since:
            for ev in console_log.tail(since_ts=since, limit=500):
                yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
        for ev in console_log.subscribe(timeout=15.0):
            if ev.get("kind") == "heartbeat":
                yield ": heartbeat\n\n"
                continue
            if since and int(ev.get("ts") or 0) <= since:
                continue
            yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def run(host: str = "127.0.0.1", port: int = 8765):
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
