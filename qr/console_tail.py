"""tail launchd agent 日志并写入 console_log（Web 进程内 daemon）。"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from . import config, console_log, schedule_service

_log = logging.getLogger(__name__)

_AGENT_LABEL = {label.rsplit(".", 1)[-1]: label for label in schedule_service.AGENT_LABELS}
_started = False
_start_lock = threading.Lock()
_offsets: dict[str, int] = {}
_rate: dict[str, tuple[float, int]] = {}
_MAX_LINES_PER_SEC = 5


def _emit_line(agent: str, kind: str, text: str) -> None:
    text = console_log.strip_ansi(text.rstrip("\n"))
    if not text:
        return
    now = time.time()
    bucket = _rate.get(agent, (0.0, 0))
    if now - bucket[0] >= 1.0:
        bucket = (now, 0)
    if bucket[1] >= _MAX_LINES_PER_SEC:
        _rate[agent] = bucket
        return
    _rate[agent] = (bucket[0], bucket[1] + 1)
    console_log.emit(source="agent", kind=kind, agent=agent, text=text)


def _read_new_lines(path: Path, key: str) -> list[str]:
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    off = _offsets.get(key, 0)
    if size < off:
        off = 0
    if size == off:
        return []
    try:
        with path.open("rb") as f:
            f.seek(off)
            raw = f.read()
        _offsets[key] = size
    except OSError:
        return []
    if not raw:
        return []
    text = raw.decode("utf-8", errors="replace")
    return text.splitlines()


def _poll_once() -> None:
    for name, label in _AGENT_LABEL.items():
        out = config.LOGS_DIR / f"{name}.out.log"
        err = config.LOGS_DIR / f"{name}.err.log"
        for path, kind, key in (
            (out, "stdout", f"{name}:out"),
            (err, "stderr", f"{name}:err"),
        ):
            for line in _read_new_lines(path, key):
                _emit_line(label, kind, line)


def _loop() -> None:
    while True:
        try:
            _poll_once()
        except Exception as exc:
            _log.debug("console tail poll: %s", exc)
        time.sleep(2.0)


def start() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    config.ensure_dirs()
    t = threading.Thread(target=_loop, name="qr-console-tail", daemon=True)
    t.start()
