from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    uid     TEXT UNIQUE,
    ts      INTEGER NOT NULL,
    source  TEXT NOT NULL,
    project TEXT,
    title   TEXT,
    content TEXT,
    meta    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);

CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT UNIQUE,
    project    TEXT,
    ext        TEXT,
    mtime      REAL,
    hash       TEXT,
    n_chunks   INTEGER DEFAULT 0,
    indexed_at INTEGER
);

CREATE TABLE IF NOT EXISTS chunks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id    INTEGER NOT NULL,
    ordinal   INTEGER NOT NULL,
    text      TEXT NOT NULL,
    dim       INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

CREATE TABLE IF NOT EXISTS summaries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    period     TEXT,
    start_ts   INTEGER,
    end_ts     INTEGER,
    content    TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS standards_versions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    note       TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS project_standards_versions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project    TEXT NOT NULL,
    content    TEXT NOT NULL,
    note       TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proj_std_project ON project_standards_versions(project);

CREATE TABLE IF NOT EXISTS app_usage (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    app       TEXT NOT NULL,
    bundle    TEXT,
    start_ts  INTEGER NOT NULL,
    end_ts    INTEGER NOT NULL,
    duration  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_start ON app_usage(start_ts);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    deep       INTEGER DEFAULT 0,
    web        INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at);

CREATE TABLE IF NOT EXISTS chat_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    hits       TEXT,
    web        TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
"""


_VEC_OK: bool | None = None


def _load_vec(conn: sqlite3.Connection) -> bool:
    global _VEC_OK
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _VEC_OK = True
    except Exception:
        _VEC_OK = False
    return _VEC_OK


def vec_available() -> bool:
    return bool(_VEC_OK)


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    _load_vec(conn)
    return conn


def init_fts(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
        "chunk_id UNINDEXED, path UNINDEXED, project UNINDEXED, text, "
        "tokenize='unicode61 remove_diacritics 2'"
        ")"
    )


def rebuild_fts(conn: sqlite3.Connection) -> int:
    init_fts(conn)
    conn.execute("DELETE FROM chunks_fts")
    rows = conn.execute(
        "SELECT c.id, c.text, d.path, d.project FROM chunks c "
        "JOIN documents d ON c.doc_id=d.id"
    ).fetchall()
    for r in rows:
        conn.execute(
            "INSERT INTO chunks_fts(chunk_id, path, project, text) VALUES(?,?,?,?)",
            (r["id"], r["path"], r["project"] or "", r["text"]),
        )
    return len(rows)


def fts_index_chunk(
    conn: sqlite3.Connection,
    chunk_id: int,
    path: str,
    project: str | None,
    text: str,
) -> None:
    init_fts(conn)
    conn.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (chunk_id,))
    conn.execute(
        "INSERT INTO chunks_fts(chunk_id, path, project, text) VALUES(?,?,?,?)",
        (chunk_id, path, project or "", text),
    )


def fts_delete_doc(conn: sqlite3.Connection, doc_id: int) -> None:
    try:
        conn.execute(
            "DELETE FROM chunks_fts WHERE chunk_id IN "
            "(SELECT id FROM chunks WHERE doc_id=?)",
            (doc_id,),
        )
    except sqlite3.OperationalError:
        pass


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db_retry(*, retries: int = 12, delay: float = 0.35) -> None:
    """init_db with backoff when launchd 多进程同时打开 qr.db。"""
    last: sqlite3.OperationalError | None = None
    for attempt in range(retries):
        try:
            init_db()
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            last = e
            time.sleep(delay * (attempt + 1))
    if last:
        raise last


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "chat_sessions", "model", "TEXT")
        from . import prompt_guides, project_relations

        prompt_guides.ensure_schema(conn)
        project_relations.ensure_schema(conn)
        conn.commit()
        init_fts(conn)
        if vec_available():
            dim = int(config.load_config().get("embed_dim", 768))
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING "
                f"vec0(embedding float[{dim}] distance_metric=cosine)"
            )
        try:
            chunks_n = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
            fts_n = conn.execute("SELECT COUNT(*) c FROM chunks_fts").fetchone()["c"]
            if chunks_n and not fts_n:
                rebuild_fts(conn)
        except sqlite3.OperationalError:
            pass


def sync_vec(conn: sqlite3.Connection) -> int:
    """把 chunks 中尚未进入向量虚拟表的嵌入补齐。"""
    if not vec_available():
        return 0
    rows = conn.execute(
        "SELECT id, embedding FROM chunks "
        "WHERE id NOT IN (SELECT rowid FROM vec_chunks)"
    ).fetchall()
    for r in rows:
        conn.execute("INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                     (r["id"], r["embedding"]))
    return len(rows)


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO state(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def insert_event(conn: sqlite3.Connection, *, uid: str, ts: int, source: str,
                 title: str = "", content: str = "", project: str | None = None,
                 meta: str | None = None) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO events(uid,ts,source,project,title,content,meta) "
        "VALUES(?,?,?,?,?,?,?)",
        (uid, ts, source, project, title, content, meta),
    )
    return cur.rowcount > 0


def upsert_event(conn: sqlite3.Connection, *, uid: str, ts: int, source: str,
                 title: str = "", content: str = "", project: str | None = None,
                 meta: str | None = None) -> None:
    conn.execute(
        "INSERT INTO events(uid,ts,source,project,title,content,meta) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(uid) DO UPDATE SET "
        "ts=excluded.ts, source=excluded.source, project=excluded.project, "
        "title=excluded.title, content=excluded.content, meta=excluded.meta",
        (uid, ts, source, project, title, content, meta),
    )


def now() -> int:
    return int(time.time())
