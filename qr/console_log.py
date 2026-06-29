"""统一操作控制台日志：Web / CLI / launchd 写入 JSONL，供终端标签页与 SSE 消费。"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Iterator

from . import config

_LOG_PATH = config.LOGS_DIR / "console.jsonl"
_MAX_LINES = 5000
_MAX_BYTES = 2 * 1024 * 1024
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_lock = threading.Lock()
_subscribers: list[tuple[threading.Condition, deque]] = []
_active_jobs: dict[str, dict[str, Any]] = {}
_line_count = 0


def log_path() -> Path:
    return _LOG_PATH


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _ensure_dir() -> None:
    config.ensure_dirs()


def _maybe_rotate() -> None:
    global _line_count
    if _line_count < _MAX_LINES and _LOG_PATH.exists():
        try:
            if _LOG_PATH.stat().st_size <= _MAX_BYTES:
                return
        except OSError:
            return
    if not _LOG_PATH.is_file():
        _line_count = 0
        return
    try:
        lines = _LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    keep = lines[-_MAX_LINES:]
    _line_count = len(keep)
    try:
        _LOG_PATH.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    except OSError:
        pass


def _notify_subscribers(event: dict[str, Any]) -> None:
    dead: list[tuple[threading.Condition, deque]] = []
    for cond, buf in list(_subscribers):
        with cond:
            buf.append(event)
            cond.notify_all()
    for item in dead:
        if item in _subscribers:
            _subscribers.remove(item)


def _track_job(event: dict[str, Any]) -> None:
    job_id = event.get("job_id")
    if not job_id:
        return
    kind = event.get("kind")
    if kind == "start":
        _active_jobs[job_id] = {
            "job_id": job_id,
            "label": event.get("label") or job_id,
            "source": event.get("source"),
            "started_at": event.get("ts") or int(time.time()),
        }
    elif kind in ("done", "error"):
        _active_jobs.pop(job_id, None)


def emit(
    *,
    source: str,
    kind: str,
    text: str = "",
    label: str = "",
    job_id: str = "",
    agent: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """追加一条控制台事件；返回写入的事件 dict。"""
    _ensure_dir()
    event: dict[str, Any] = {
        "ts": int(time.time()),
        "source": source,
        "kind": kind,
    }
    if label:
        event["label"] = label
    if job_id:
        event["job_id"] = job_id
    if agent:
        event["agent"] = agent
    if text:
        event["text"] = text[:8000]
    if extra:
        event.update(extra)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    global _line_count
    with _lock:
        _maybe_rotate()
        try:
            fd = os.open(str(_LOG_PATH), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
            _line_count += 1
            if _line_count >= _MAX_LINES:
                _maybe_rotate()
        except OSError:
            pass
        _track_job(event)
    _notify_subscribers(event)
    return event


def new_job_id(prefix: str = "job") -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6]}"


def job_start(
    *,
    source: str,
    label: str,
    job_id: str | None = None,
    text: str = "",
) -> str:
    jid = job_id or new_job_id()
    emit(source=source, kind="start", label=label, job_id=jid, text=text)
    return jid


def job_done(
    job_id: str,
    *,
    source: str,
    label: str = "",
    text: str = "",
    error: bool = False,
) -> None:
    emit(
        source=source,
        kind="error" if error else "done",
        label=label,
        job_id=job_id,
        text=text,
    )


def job_progress(
    job_id: str,
    *,
    source: str,
    text: str,
    label: str = "",
    throttle_key: str = "",
    min_interval: float = 2.0,
) -> None:
    """节流 progress 事件（同一 throttle_key 默认 2s 一条）。"""
    if not hasattr(job_progress, "_last"):
        job_progress._last = {}  # type: ignore[attr-defined]
    key = throttle_key or job_id
    now = time.time()
    last = job_progress._last.get(key, 0.0)  # type: ignore[attr-defined]
    if now - last < min_interval:
        return
    job_progress._last[key] = now  # type: ignore[attr-defined]
    emit(source=source, kind="progress", label=label, job_id=job_id, text=text)


def tail(
    *,
    since_ts: int = 0,
    limit: int = 200,
    source: str = "",
    agent: str = "",
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 1000))
    if not _LOG_PATH.is_file():
        return []
    try:
        lines = _LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(out) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since_ts and int(ev.get("ts") or 0) <= since_ts:
            continue
        if source and ev.get("source") != source:
            continue
        if agent and ev.get("agent") != agent:
            continue
        out.append(ev)
    out.reverse()
    return out


def active_jobs() -> list[dict[str, Any]]:
    with _lock:
        return sorted(_active_jobs.values(), key=lambda j: j.get("started_at") or 0)


def subscribe(timeout: float = 15.0) -> Iterator[dict[str, Any]]:
    """阻塞生成器：yield 新事件；timeout 秒无事件则 yield heartbeat。"""
    cond = threading.Condition()
    buf: deque = deque()
    with _lock:
        _subscribers.append((cond, buf))
    try:
        while True:
            with cond:
                if not buf:
                    cond.wait(timeout=timeout)
                if buf:
                    while buf:
                        yield buf.popleft()
                else:
                    yield {"ts": int(time.time()), "kind": "heartbeat"}
    finally:
        with _lock:
            if (cond, buf) in _subscribers:
                _subscribers.remove((cond, buf))


def agent_log_files() -> list[dict[str, Any]]:
    """launchd agent 对应 stdout 日志路径。"""
    from . import ops_panel, schedule_service

    rows: list[dict[str, Any]] = []
    for label in schedule_service.AGENT_LABELS:
        name = label.rsplit(".", 1)[-1]
        out_path = config.LOGS_DIR / f"{name}.out.log"
        err_path = config.LOGS_DIR / f"{name}.err.log"
        mtime = None
        if out_path.is_file():
            try:
                mtime = int(out_path.stat().st_mtime)
            except OSError:
                pass
        rows.append({
            "label": label,
            "name": name,
            "title": ops_panel._AGENT_TITLES.get(label, label),
            "out_log": str(out_path),
            "err_log": str(err_path),
            "mtime": mtime,
        })
    return rows
