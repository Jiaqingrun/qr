"""知识库操作时间线：将 QR 系统内的人为操作实时写入 events（source=qr）。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from . import db

_log = logging.getLogger(__name__)

SOURCE = "qr"
_KIND = "qr_operation"

# 后台自动触发、永不写入时间线（避免 Cursor 增量采集刷屏）
_NO_TIMELINE_PATHS = frozenset({
    "/api/ingest/cursor",
})


def skip_timeline_path(path: str) -> bool:
    p = (path or "").split("?", 1)[0]
    return p in _NO_TIMELINE_PATHS or any(
        p.startswith(prefix + "/") for prefix in _NO_TIMELINE_PATHS
    )

# 不重复记录或只读的 API
_SKIP_PATHS = frozenset({
    "/api/status",
    "/api/events",
    "/api/open",
    "/api/ask/models",
    "/api/projects",
    "/api/categories",
    "/api/workspace/project/delete-preview",
    "/api/chats",
    "/api/summaries",
    "/api/standards",
    "/api/standards/changelog",
    "/api/standards/history",
    "/api/standards/changelog/prune",
    "/api/project/brief",
    "/api/today",
    "/api/symbol",
    "/api/changelog",
    "/api/alerts",
    "/api/digest",
    "/api/notify",
    "/api/project",
    "/api/project/focus",
    "/api/facts",
    "/api/prompts/stats",
    "/api/prompts/types",
    "/api/prompts/fragments",
    "/api/prompts/inbox-groups",
    "/api/prompts/guides",
    "/api/eval/cases",
    "/api/eval/regression",
    "/api/eval/history",
    "/api/eval/failures",
    "/api/eval/fixplan",
    "/api/eval",
    "/api/compliance",
    "/api/graph",
    "/api/projects/relations",
    "/api/usage",
})

# 已有专用 events 来源，避免双写
_SKIP_PREFIXES = (
    "/api/log",  # → source=note
)

_SKIP_SUFFIX_LOG = (
    "/api/workspace/project/delete",  # workspace 写详细删除记录
)


def _uid(action: str, ts: int) -> str:
    h = hashlib.sha1(f"{action}:{ts}".encode()).hexdigest()[:10]
    return f"qr:{action}:{ts}:{h}"


def _meta(**extra: Any) -> str:
    payload = {"kind": _KIND, **extra}
    return json.dumps(payload, ensure_ascii=False)


def log(
    conn,
    *,
    action: str,
    title: str,
    content: str = "",
    project: str | None = None,
    via: str = "web",
    extra: dict[str, Any] | None = None,
) -> None:
    ts = db.now()
    meta = {"via": via, "action": action}
    if extra:
        meta.update(extra)
    db.upsert_event(
        conn,
        uid=_uid(action, ts),
        ts=ts,
        source=SOURCE,
        project=project,
        title=title[:240],
        content=content[:8000],
        meta=_meta(**meta),
    )


def log_safe(
    *,
    action: str,
    title: str,
    content: str = "",
    project: str | None = None,
    via: str = "web",
    extra: dict[str, Any] | None = None,
) -> None:
    try:
        with db.session() as conn:
            log(
                conn,
                action=action,
                title=title,
                content=content,
                project=project,
                via=via,
                extra=extra,
            )
    except Exception as exc:
        _log.warning("ops_timeline log failed: %s", exc)


def log_project_delete(
    conn,
    *,
    pid: str,
    preview: dict[str, Any],
    stats: dict[str, Any],
    via: str = "web",
) -> None:
    path = preview.get("path") or stats.get("path") or ""
    lines = [
        f"通过 {via} 永久删除项目 {pid}",
        f"路径: {path or '（无本地工作区目录，仅清理索引）'}",
        "",
        "已清理:",
        f"· 索引文档 {stats.get('documents', preview.get('counts', {}).get('documents', 0))} · "
        f"向量块 {stats.get('chunks', preview.get('counts', {}).get('chunks', 0))}",
        f"· Cursor 目录 {stats.get('cursor_dirs', 0)} · 转录 {stats.get('cursor_transcripts', 0)}",
        f"· 问答会话 {stats.get('chat_sessions_deleted', 0)} · 稳定事实 {stats.get('facts_removed', 0)}",
        f"· 本地目录: {'已删除' if stats.get('disk_removed') else '无/未删除'}",
        "",
        f"已保留时间线 {stats.get('events_retained', preview.get('counts', {}).get('events', 0))} 条；引导语库未删。",
    ]
    log(
        conn,
        action="project.delete",
        title=f"[知识库] 删除项目 {pid}",
        content="\n".join(lines),
        project=pid,
        via=via,
        extra={"stats": stats},
    )


def _pick_project(body: dict[str, Any], query: dict[str, str]) -> str | None:
    for key in ("project", "category"):
        v = body.get(key) or query.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return None


def _snippet(body: dict[str, Any], *keys: str, limit: int = 200) -> str:
    for k in keys:
        v = body.get(k)
        if v is None:
            continue
        if isinstance(v, (list, dict)):
            s = json.dumps(v, ensure_ascii=False)
        else:
            s = str(v).strip()
        if s:
            return s[:limit]
    return ""


def _path_id(path: str, prefix: str) -> str | None:
    m = re.match(rf"^{re.escape(prefix)}/(\d+)", path)
    return m.group(1) if m else None


def describe_http(
    method: str,
    path: str,
    body: dict[str, Any],
    query: dict[str, str],
) -> tuple[str, str, str, str | None] | None:
    """返回 action, title, content, project；None 表示跳过。"""
    if method not in ("POST", "PUT", "DELETE", "PATCH"):
        return None
    if not path.startswith("/api/"):
        return None
    if skip_timeline_path(path):
        return None
    if path in _SKIP_PATHS:
        return None
    for p in _SKIP_PREFIXES:
        if path == p or path.startswith(p + "/"):
            return None
    for p in _SKIP_SUFFIX_LOG:
        if path == p or path.startswith(p + "/"):
            return None

    project = _pick_project(body, query)
    action = f"{method.lower()}:{path}"

    # --- 路由语义 ---
    if path == "/api/ingest":
        return "ingest", "[知识库] 行为采集", "Web 触发增量 ingest（shell / git / 文件 / Cursor / 笔记）", project
    if path == "/api/backfill":
        days = query.get("days") or body.get("days") or "365"
        return "backfill", "[知识库] 历史补录", f"补录近 {days} 天行为数据", project
    if skip_timeline_path(path):
        return None
    if path == "/api/index":
        reindex = body.get("reindex") or query.get("reindex")
        return "index", "[知识库] 建立索引", f"{'全量重建' if reindex else '增量'}索引 ~/QR 工作区", project
    if path == "/api/query":
        q = _snippet(body, "text", "q", limit=120)
        return "query", "[知识库] 语义检索", q or "（无关键词）", project
    if path == "/api/ask":
        q = _snippet(body, "question", limit=160)
        m = body.get("model") or ""
        return "ask", "[知识库] 本地问答", f"{q}\n\n模型: {m or '默认'}", project
    if path == "/api/summary":
        p = body.get("period") or "week"
        return "summary", "[知识库] 生成总结", f"周期: {p}", project
    if path == "/api/standards/revise":
        return "standards.revise", "[知识库] AI 修订规范", _snippet(body, "instruction", limit=300), project
    if path == "/api/standards/restore":
        return "standards.restore", "[知识库] 恢复标准模板", "从仓库模板覆盖当前生效规范", project
    if path == "/api/standards/activate":
        return "standards.activate", "[知识库] 启用规范版本", f"version_id={body.get('version_id')}", project
    if path == "/api/standards" and method == "PUT":
        return "standards.save", "[知识库] 保存规范", _snippet(body, "note", limit=80) or "Web 编辑 standards.md", project
    if path == "/api/digest/notify":
        return "digest.notify", "[知识库] 每日洞察通知", f"days={query.get('days', '1')}", project
    if path == "/api/project/approve":
        return "project.approve", "[知识库] 认可项目结项", f"项目 {body.get('project')} · approved={body.get('approved')}", body.get("project")
    if path == "/api/projects/relations/infer":
        return "relations.infer", "[知识库] 推断项目关系", f"days={body.get('days', 30)}", project
    if path == "/api/projects/relations/links" and method == "POST":
        return (
            "relations.link",
            "[知识库] 保存项目关系",
            f"{body.get('from_project')} → {body.get('to_project')} ({body.get('link_type')})",
            body.get("from_project") or project,
        )
    if path.startswith("/api/projects/relations/suites") and method in ("POST", "PUT", "DELETE"):
        return "relations.suite", "[知识库] 编辑项目组合", _snippet(body, "name", "description", limit=200), project
    if path == "/api/projects/relations/meta" and method == "PUT":
        return "relations.meta", "[知识库] 更新项目角色说明", f"{body.get('project')} · {body.get('role')}", body.get("project")
    if path == "/api/facts":
        return "facts.add", "[知识库] 添加稳定事实", f"{body.get('key')}: {body.get('value')}", body.get("project") or project
    if path == "/api/facts/sync":
        return "facts.sync", "[知识库] 同步稳定事实", "从 config 写入 facts.json", project
    if path == "/api/prompts/sync":
        return "prompts.sync", "[知识库] 同步引导语收件箱", "从 Cursor 事件拉取碎片", project
    if path == "/api/prompts/reclassify":
        return "prompts.reclassify", "[知识库] 重分类引导语", "", project
    if path == "/api/prompts/repair-times":
        return "prompts.repair_times", "[知识库] 修复引导语时间", "", project
    if path == "/api/prompts/fragments/delete":
        n = len(body.get("fragment_ids") or [])
        return "prompts.fragments.delete", "[知识库] 删除问话碎片", f"{n} 条碎片", project
    if path == "/api/prompts/sessions/delete":
        n = len(body.get("session_ids") or [])
        return "prompts.sessions.delete", "[知识库] 删除 Cursor 对话", f"{n} 个会话", project
    if path == "/api/prompts/merge":
        return "prompts.merge", "[知识库] 合并引导语", f"片段 {len(body.get('fragment_ids') or [])} 条", project
    if path == "/api/prompts/guides" and method == "POST":
        return "prompts.guide.create", "[知识库] 新建引导语", _snippet(body, "title", limit=80), project
    if path == "/api/export/obsidian":
        return "export.obsidian", "[知识库] 导出 Obsidian", "", project
    if path == "/api/eval/execute":
        return "eval.execute", "[知识库] 执行评测修复", _snippet(body, "actions", limit=120), project
    if path == "/api/eval/run":
        return "eval.run", "[知识库] 运行模型评测", "", project
    if path == "/api/eval/decision-draft":
        return "eval.decision", "[知识库] 生成决策草稿", "", project
    if path == "/api/eval/cases" and method == "POST":
        return "eval.case.add", "[知识库] 添加评测题", body.get("id") or "", project

    if path.startswith("/api/chats/") and method == "DELETE":
        sid = _path_id(path, "/api/chats")
        return "chat.delete", "[知识库] 删除问答会话", f"session_id={sid}", project
    if path.startswith("/api/prompts/guides/") and method == "DELETE":
        gid = _path_id(path, "/api/prompts/guides")
        return "prompts.guide.delete", "[知识库] 删除引导语", f"guide_id={gid}", project
    if path.startswith("/api/prompts/fragments/") and method == "PATCH":
        fid = _path_id(path, "/api/prompts/fragments")
        return "prompts.fragment.patch", "[知识库] 修改问话碎片", f"fragment_id={fid}", project
    if path.startswith("/api/prompts/types") and method == "POST":
        return "prompts.type.add", "[知识库] 添加引导语类型", _snippet(body, "name", limit=60), project

    # 通用回退
    title = f"[知识库] {method} {path}"
    content = _snippet(body, "text", "question", "content", "title", "note", limit=400)
    if not content and body:
        content = json.dumps(body, ensure_ascii=False)[:400]
    return action.replace("/", "."), title, content, project


_CLI_LABELS: dict[str, str] = {
    "ingest": "行为采集",
    "index": "建立索引",
    "ask": "本地问答",
    "query": "语义检索",
    "log": "记录笔记",
    "summary": "生成总结",
    "web": "启动 Web",
    "workspace": "工作区",
    "prompts": "引导语",
    "standards": "个人规范",
    "doctor": "系统自检",
    "backup": "备份数据库",
    "init": "初始化",
}


def cli_label(argv: list[str]) -> str:
    if not argv:
        return "qr"
    cmd = argv[0]
    sub = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else ""
    label = _CLI_LABELS.get(cmd, cmd)
    if sub:
        label = f"{label} · {sub}"
    return label


def log_cli(argv: list[str]) -> None:
    if not argv or argv[0] in ("--help", "-h", "completion"):
        return
    cmd = argv[0]
    sub = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else ""
    action = f"cli:{cmd}" + (f".{sub}" if sub else "")
    label = cli_label(argv)
    detail = " ".join(argv)[:500]
    log_safe(
        action=action,
        title=f"[知识库] CLI · {label}",
        content=detail,
        via="cli",
    )


def should_log_http(method: str, path: str, status_code: int) -> bool:
    if status_code >= 400:
        return False
    if skip_timeline_path(path):
        return False
    if describe_http(method, path, {}, {}) is None:
        return False
    return True
