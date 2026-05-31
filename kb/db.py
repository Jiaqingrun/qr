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
"""


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


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


def now() -> int:
    return int(time.time())
