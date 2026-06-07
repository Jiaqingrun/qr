"""新增功能单元测试（轻量、隔离临时库）。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import (
    backup_ops,
    chunking,
    event_links,
    index_health,
    reranker,
    resume_panel,
    symbol_index,
    timeline_search,
)


def _temp_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, uid TEXT UNIQUE, ts INTEGER, "
        "source TEXT, project TEXT, title TEXT, content TEXT, meta TEXT);"
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, path TEXT UNIQUE, project TEXT, "
        "ext TEXT, mtime REAL, hash TEXT, n_chunks INTEGER, indexed_at INTEGER);"
        "CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id INTEGER, ordinal INTEGER, "
        "text TEXT, dim INTEGER, embedding BLOB);"
        "CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT);"
    )
    return conn


class TestBackupOps(unittest.TestCase):
    def test_verify_backup_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "qr-test.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                "CREATE TABLE events (id INTEGER);"
                "CREATE TABLE documents (id INTEGER);"
                "CREATE TABLE chunks (id INTEGER);"
                "CREATE TABLE state (key TEXT);"
            )
            conn.execute("INSERT INTO events DEFAULT VALUES")
            conn.commit()
            conn.close()
            rep = backup_ops.verify_backup(path)
            self.assertTrue(rep["ok"])


class TestTimelineSearch(unittest.TestCase):
    def test_fts_search(self):
        conn = _temp_conn()
        timeline_search.ensure_schema(conn)
        conn.execute(
            "INSERT INTO events(uid,ts,source,project,title,content) VALUES(?,?,?,?,?,?)",
            ("u1", 100, "note", "qr", "SQLite 决策", "选用 SQLite"),
        )
        timeline_search.index_event(
            conn, uid="u1", source="note", project="qr",
            title="SQLite 决策", content="选用 SQLite",
        )
        conn.commit()
        with mock.patch("qr.timeline_search.workspace.events_project_sql_filter", return_value=("1=1", [])), mock.patch(
            "qr.timeline_search.workspace.events_timeline_hidden_sql", return_value="1=1",
        ), mock.patch("qr.timeline_search.workspace.event_row_visible", return_value=True), mock.patch(
            "qr.timeline_search.workspace.event_timeline_hidden", return_value=False,
        ):
            hits = timeline_search.search(conn, "SQLite")
        self.assertEqual(len(hits), 1)
        conn.close()


class TestEventLinks(unittest.TestCase):
    def test_related_by_path(self):
        conn = _temp_conn()
        path = "/Users/qr/QR/dev/qr/config.py"
        conn.execute(
            "INSERT INTO events(uid,ts,source,project,title,content) VALUES(?,?,?,?,?,?)",
            ("g1", 1000, "git", "qr", "fix", path),
        )
        conn.execute(
            "INSERT INTO events(uid,ts,source,project,title,content) VALUES(?,?,?,?,?,?)",
            ("c1", 1010, "cursor", "qr", "改 config", path),
        )
        conn.commit()
        with mock.patch("qr.event_links.workspace.events_project_sql_filter", return_value=("1=1", [])), mock.patch(
            "qr.event_links.workspace.events_timeline_hidden_sql", return_value="1=1",
        ), mock.patch("qr.event_links.workspace.event_row_visible", return_value=True), mock.patch(
            "qr.event_links.workspace.event_timeline_hidden", return_value=False,
        ):
            rel = event_links.related_for_event(
                conn, uid="g1", source="git", title="fix", content=path,
                meta=None, ts=1000,
            )
        self.assertTrue(any(r["uid"] == "c1" for r in rel))
        conn.close()


class TestChunking(unittest.TestCase):
    def test_code_chunks(self):
        raw = "def foo():\n    return 1\n\nclass Bar:\n    pass\n"
        parts = chunking.chunk_document(Path("x.py"), raw, {"chunk_chars": 80, "code_aware_chunking": True})
        self.assertGreaterEqual(len(parts), 1)


class TestReranker(unittest.TestCase):
    def test_lexical_boost(self):
        hits = [{"text": "QR 知识库 Web 端口 8765", "score": 0.5, "path": "/a"}]
        out = reranker.rerank_hits("Web 端口", hits, 1)
        self.assertGreaterEqual(out[0]["score"], 0.5)


class TestIndexHealth(unittest.TestCase):
    def test_missing_path(self):
        conn = _temp_conn()
        conn.execute(
            "INSERT INTO documents(path,project,ext,mtime,hash,n_chunks,indexed_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("/no/such/file.md", "dev/x", ".md", 0.0, "h", 0, 0),
        )
        conn.commit()
        rep = index_health.scan(conn)
        self.assertEqual(rep["missing_files"], 1)
        conn.close()


class TestSymbolIndex(unittest.TestCase):
    def test_extract_and_search(self):
        conn = _temp_conn()
        symbol_index.ensure_schema(conn)
        raw = "def load_stats():\n    pass\n\nclass ResumePanel:\n    pass\n"
        path = Path("/tmp/test_sym.py")
        n = symbol_index.sync_path(conn, path, "dev/qr", raw)
        self.assertEqual(n, 2)
        syms = symbol_index.extract_symbols(path, raw)
        self.assertTrue(any(s["name"] == "load_stats" for s in syms))
        rows = conn.execute(
            "SELECT name FROM symbols WHERE lower(name)=lower(?)",
            ("load_stats",),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        symbol_index.remove_path(conn, str(path.resolve()))
        conn.commit()
        left = conn.execute("SELECT COUNT(*) c FROM symbols").fetchone()["c"]
        self.assertEqual(left, 0)
        conn.close()


class TestResumePanel(unittest.TestCase):
    def test_generate_minimal(self):
        conn = _temp_conn()
        with mock.patch("qr.resume_panel.project_brief.detect_active_project", return_value=(None, "none")), mock.patch(
            "qr.resume_panel.project_brief.brief",
            return_value={"project": "", "lines": [], "feature_tasks": [], "opt_tasks": []},
        ), mock.patch("qr.resume_panel.prompt_guides.stats", return_value={"inbox": 0, "guides": 0}), mock.patch(
            "qr.resume_panel._workspace_open_tasks", return_value=[],
        ):
            out = resume_panel.generate(conn)
        self.assertIn("actions", out)
        self.assertIn("cursor_topics", out)
        conn.close()


class TestIncrementalIndexer(unittest.TestCase):
    def test_since_ts_from_cfg(self):
        from contextlib import contextmanager

        from qr import indexer

        conn = _temp_conn()
        conn.execute("INSERT INTO state(key,value) VALUES(?,?)", ("ingest_last_ts", "99000"))
        conn.commit()

        @contextmanager
        def fake_session():
            yield conn

        with mock.patch("qr.indexer.db.session", fake_session):
            ts = indexer._since_ts_from_cfg({"index_incremental_after_ingest": True}, None, None)
        self.assertEqual(ts, 99000)
        conn.close()


class TestWebNewApi(unittest.TestCase):
    def test_events_search_q(self):
        from fastapi.testclient import TestClient
        from qr.web import app

        client = TestClient(app)
        with mock.patch("qr.timeline_search.search", return_value=[]):
            r = client.get("/api/events?q=SQLite")
        self.assertEqual(r.status_code, 200)
        self.assertIn("items", r.json())

    def test_index_health_api(self):
        from fastapi.testclient import TestClient
        from qr.web import app

        client = TestClient(app)
        with mock.patch("qr.ops_panel.index_health", return_value={"documents": 1, "missing_files": 0}):
            r = client.post("/api/ops/index-health", json={"cleanup": False})
        self.assertEqual(r.status_code, 200)

    def test_alerts_api(self):
        from fastapi.testclient import TestClient
        from qr.web import app

        client = TestClient(app)
        with mock.patch("qr.proactive.collect_all", return_value=[]):
            r = client.get("/api/alerts")
        self.assertEqual(r.status_code, 200)

    def test_resume_api(self):
        from fastapi.testclient import TestClient
        from qr.web import app

        client = TestClient(app)
        with mock.patch(
            "qr.resume_panel.generate",
            return_value={"active_project": "dev/qr", "actions": []},
        ):
            r = client.get("/api/resume")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["active_project"], "dev/qr")


if __name__ == "__main__":
    unittest.main()
