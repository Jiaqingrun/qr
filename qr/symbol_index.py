"""符号索引：函数 / 类 / 定义 精确查找。"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from . import config, db, workspace

_SYMBOL_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".swift", ".kt", ".java"}

_PY_SYM = re.compile(
    r"^(?:async\s+)?def\s+(\w+)|^class\s+(\w+)",
    re.MULTILINE,
)
_JS_SYM = re.compile(
    r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)|"
    r"^(?:export\s+)?class\s+(\w+)|"
    r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(",
    re.MULTILINE,
)
_GO_SYM = re.compile(r"^func\s+(?:\([^)]*\)\s+)?(\w+)", re.MULTILINE)
_RS_SYM = re.compile(r"^fn\s+(\w+)", re.MULTILINE)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS symbols ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "path TEXT NOT NULL,"
        "project TEXT,"
        "name TEXT NOT NULL,"
        "kind TEXT NOT NULL,"
        "line INTEGER NOT NULL,"
        "UNIQUE(path, name, kind)"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(lower(name))"
    )


def _kind_for_match(groups: tuple) -> tuple[str, str]:
    for i, g in enumerate(groups):
        if g:
            kinds = ("function", "class", "function", "function")
            return g, kinds[min(i, len(kinds) - 1)]
    return "", "symbol"


def extract_symbols(path: Path, text: str) -> list[dict[str, Any]]:
    ext = path.suffix.lower()
    if ext not in _SYMBOL_EXTS:
        return []
    patterns = []
    if ext == ".py":
        patterns.append(_PY_SYM)
    elif ext in {".js", ".ts", ".tsx", ".jsx"}:
        patterns.append(_JS_SYM)
    elif ext == ".go":
        patterns.append(_GO_SYM)
    elif ext == ".rs":
        patterns.append(_RS_SYM)
    else:
        patterns.append(_PY_SYM)
    found: dict[tuple[str, str], dict] = {}
    for pat in patterns:
        for m in pat.finditer(text):
            line = text[: m.start()].count("\n") + 1
            name, kind = _kind_for_match(m.groups())
            if not name or name.startswith("_") and name != "__init__":
                if name.startswith("__") and not name.endswith("__"):
                    pass
                elif name.startswith("_"):
                    continue
            key = (name, kind)
            if key not in found:
                found[key] = {"name": name, "kind": kind, "line": line}
    return list(found.values())


def sync_path(
    conn: sqlite3.Connection,
    path: Path,
    project: str,
    text: str,
) -> int:
    ensure_schema(conn)
    path_s = str(path.resolve())
    conn.execute("DELETE FROM symbols WHERE path=?", (path_s,))
    n = 0
    for sym in extract_symbols(path, text):
        conn.execute(
            "INSERT OR REPLACE INTO symbols(path,project,name,kind,line) VALUES(?,?,?,?,?)",
            (path_s, project, sym["name"], sym["kind"], int(sym["line"])),
        )
        n += 1
    return n


def remove_path(conn: sqlite3.Connection, path: str) -> None:
    ensure_schema(conn)
    conn.execute("DELETE FROM symbols WHERE path=?", (path,))


def search(
    name: str,
    *,
    project: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    name = (name or "").strip()
    if not name:
        return []
    ensure_schema(conn := db.connect())
    try:
        rows = conn.execute(
            "SELECT path, project, name, kind, line FROM symbols "
            "WHERE lower(name)=lower(?) OR lower(name) LIKE ? "
            "ORDER BY CASE WHEN lower(name)=lower(?) THEN 0 ELSE 1 END, path LIMIT ?",
            (name, f"%{name.lower()}%", name, limit * 2),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        if project:
            pl = project.lower()
            dp = (r["project"] or "").lower()
            path_l = (r["path"] or "").lower()
            if pl not in dp and pl not in path_l:
                continue
        out.append({
            "path": r["path"],
            "project": workspace.sanitize_display_project(r["project"]),
            "name": r["name"],
            "kind": r["kind"],
            "line": int(r["line"]),
        })
        if len(out) >= limit:
            break
    return out


def stats(conn: sqlite3.Connection | None = None) -> dict[str, int]:
    own = conn is None
    if own:
        conn = db.connect()
    try:
        ensure_schema(conn)
        total = int(conn.execute("SELECT COUNT(*) c FROM symbols").fetchone()["c"])
        files = int(conn.execute("SELECT COUNT(DISTINCT path) c FROM symbols").fetchone()["c"])
        return {"symbols": total, "files": files}
    finally:
        if own:
            conn.close()
