"""新增功能单元测试（轻量、隔离临时库）。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import (
    backup_ops,
    chunking,
    event_links,
    facts,
    index_health,
    query,
    reranker,
    resume_panel,
    retrieval_boost,
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
        self.assertEqual(out[0]["scores"]["final"], out[0]["score"])


class TestRetrievalBoost(unittest.TestCase):
    def test_config_path_boost(self):
        p = "/Users/qr/.qr/config.json"
        q = "QR本地知识库 config.json 里 context_tokens 是多少"
        b = retrieval_boost.path_boost(
            p, q, is_qr_query=query._is_qr_query, project_ref_boost=query._project_ref_boost,
        )
        self.assertGreaterEqual(b, 0.5)

    def test_dedupe_by_path(self):
        hits = [
            {"path": "/a", "score": 1.0},
            {"path": "/a", "score": 0.9},
            {"path": "/a", "score": 0.8},
            {"path": "/b", "score": 0.7},
        ]
        out = retrieval_boost.dedupe_by_path(hits, 3, max_per_path=2)
        self.assertEqual(len(out), 3)
        self.assertEqual(sum(1 for h in out if h["path"] == "/a"), 2)

    def test_vec_fetch_limit_filtered(self):
        self.assertGreater(
            retrieval_boost.vec_fetch_limit(6, "dev/qr", None, {"retrieval_vec_oversample": 8}),
            retrieval_boost.vec_fetch_limit(6, None, None, {}),
        )


class TestCursorBubbleTime(unittest.TestCase):
    def test_apply_precise_times(self):
        import sqlite3
        import tempfile
        from qr import cursor_bubble_time

        with tempfile.TemporaryDirectory() as td:
            dbp = Path(td) / "state.vscdb"
            conn = sqlite3.connect(dbp)
            conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
            sid = "sess-1"
            payload = json.dumps({
                "text": "测试问话精确时间",
                "createdAt": "2026-06-07T08:00:00.000Z",
                "type": 1,
            })
            conn.execute(
                "INSERT INTO cursorDiskKV VALUES (?, ?)",
                (f"bubbleId:{sid}:b1", payload),
            )
            conn.commit()
            conn.close()
            turns = [{"query": "测试问话精确时间", "reply": "", "ts": 1, "ts_estimated": True}]
            cfg = {"cursor_precise_time": True, "cursor_state_db": str(dbp)}
            n = cursor_bubble_time.apply_precise_times(sid, turns, cfg=cfg)
            self.assertEqual(n, 1)
            self.assertFalse(turns[0]["ts_estimated"])
            self.assertGreater(turns[0]["ts"], 1_700_000_000)


class TestFactsSync(unittest.TestCase):
    def test_sync_includes_launchd(self):
        with mock.patch("qr.facts.add_fact", side_effect=lambda k, v, **kw: {"key": k, "value": v}):
            rows = facts.sync_from_config()
        keys = [r["key"] for r in rows]
        self.assertIn("launchd_schedule_install", keys)
        launchd = next(r for r in rows if r["key"] == "launchd_schedule_install")
        self.assertIn("com.qr.auto", launchd["value"])

    def test_restore_report_facts(self):
        with mock.patch("qr.facts.sync_from_config", return_value=[]), mock.patch(
            "qr.facts.add_fact", side_effect=lambda k, v, **kw: {"key": k, "value": v, **kw},
        ):
            rows = facts.restore_report_facts()
        keys = {(r["key"], r.get("project")) for r in rows}
        self.assertIn(("mvp_focus", "dev/sports/project-sports"), keys)
        self.assertIn(("retrieval_upgrade_policy", "QR"), keys)
        self.assertIn(("cursor_workspace", "dev/scribe"), keys)


class TestFactsRetrieval(unittest.TestCase):
    def test_retrieval_hits_port(self):
        with mock.patch("qr.facts.list_facts", return_value=[
            {"key": "web_port", "value": "8765", "project": "QR"},
        ]):
            hits = facts.retrieval_hits("Web 默认端口是多少")
        self.assertEqual(len(hits), 1)
        self.assertIn("8765", hits[0]["text"])
        self.assertTrue(hits[0].get("fact"))


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

    def test_events_include_related_default_off(self):
        from fastapi.testclient import TestClient
        from qr.web import app

        client = TestClient(app)
        with mock.patch("qr.event_links.related_for_event") as rel:
            r = client.get("/api/events?limit=5")
            self.assertEqual(r.status_code, 200)
            rel.assert_not_called()

    def test_status_dashboard_cache(self):
        from qr import db, health

        health.invalidate_status_cache()
        with db.session() as conn:
            health.status_dashboard(conn)
            t0 = __import__("time").perf_counter()
            health.status_dashboard(conn)
            elapsed = __import__("time").perf_counter() - t0
        self.assertLess(elapsed, 0.05)

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

    def test_today_api(self):
        from fastapi.testclient import TestClient
        from qr.web import app

        client = TestClient(app)
        with mock.patch(
            "qr.today_panel.generate",
            return_value={"active_project": "dev/qr", "alerts": []},
        ):
            r = client.get("/api/today")
        self.assertEqual(r.status_code, 200)

    def test_project_brief_api(self):
        from fastapi.testclient import TestClient
        from qr.web import app

        client = TestClient(app)
        with mock.patch("qr.project_brief.brief", return_value={"project": "dev/qr", "lines": []}):
            r = client.get("/api/project/brief?project=qr")
        self.assertEqual(r.status_code, 200)

    def test_standards_history_alias(self):
        from fastapi.testclient import TestClient
        from qr.web import app

        client = TestClient(app)
        with mock.patch("qr.governance.ensure_standards"), mock.patch(
            "qr.standards_changelog.build_changelog", return_value={"entries": []},
        ):
            r = client.get("/api/standards/history")
        self.assertEqual(r.status_code, 200)

    def test_changelog_api(self):
        from fastapi.testclient import TestClient
        from qr.web import app

        client = TestClient(app)
        with mock.patch(
            "qr.changelog.generate",
            return_value={"project": "dev/qr", "content": "# ok", "path": "/tmp/x.md"},
        ):
            r = client.get("/api/changelog", params={"project": "dev/qr", "days": 7})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["project"], "dev/qr")

    def test_symbol_api(self):
        from fastapi.testclient import TestClient
        from qr.web import app

        client = TestClient(app)
        with mock.patch("qr.symbol_index.search", return_value=[]), mock.patch(
            "qr.symbol_index.stats", return_value={"symbols": 0, "files": 0},
        ):
            r = client.get("/api/symbol?name=loadStats")
        self.assertEqual(r.status_code, 200)
        self.assertIn("hits", r.json())

    def test_daily_plan_api(self):
        from fastapi.testclient import TestClient
        from qr.web import app

        client = TestClient(app)
        sample = {
            "date": "2026-06-08",
            "items": [
                {
                    "id": "ai-skill-assess",
                    "label": "每日 AI 水平评测",
                    "command": "qr ai-assess --save",
                    "hint": "",
                    "done": False,
                }
            ],
            "done_count": 0,
            "total": 1,
        }
        with mock.patch("qr.daily_plan.list_for_date", return_value=sample):
            r = client.get("/api/insight/daily-plan")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["items"][0]["command"], "qr ai-assess --save")

        toggled = {**sample, "done_count": 1, "items": [{**sample["items"][0], "done": True}]}
        with mock.patch("qr.daily_plan.set_done", return_value=toggled):
            r = client.post(
                "/api/insight/daily-plan/toggle",
                json={"id": "ai-skill-assess", "done": True},
            )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["items"][0]["done"])


class TestDailyPlan(unittest.TestCase):
    def test_toggle_persists(self):
        from qr import daily_plan

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "daily_plan.json"
            with mock.patch.object(daily_plan, "PLAN_PATH", path):
                first = daily_plan.list_for_date("2026-06-08")
                self.assertEqual(first["total"], 2)
                self.assertFalse(first["items"][0]["done"])
                second = daily_plan.set_done("ai-skill-assess", True, day="2026-06-08")
                self.assertTrue(second["items"][0]["done"])
                third = daily_plan.set_done("ai-skill-assess", False, day="2026-06-08")
                self.assertFalse(third["items"][0]["done"])

    def test_monthly_cadence(self):
        from qr import daily_plan

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "daily_plan.json"
            with mock.patch.object(daily_plan, "PLAN_PATH", path):
                out = daily_plan.set_done("monthly-eval", True, day="2026-06-10")
                monthly = next(i for i in out["items"] if i["id"] == "monthly-eval")
                self.assertTrue(monthly["done"])
                self.assertEqual(monthly["period"], "2026-06")


class TestMonthlyEval(unittest.TestCase):
    def test_render_report(self):
        from qr import monthly_eval

        md = monthly_eval.render_report(
            rag_rows=[{"case": "port", "retrieval_ok": True, "retrieval_forbidden": False}],
            rag_summary={
                "cases": 1,
                "retrieval_ok": 1,
                "retrieval_rate": 100.0,
                "forbidden_hits": 0,
                "search_avg": 0.5,
            },
            ai_snap={"generated_ts": 1_700_000_000, "generated_at": "2026-06-10", "cursor_by_project": {}},
            template=monthly_eval.DEFAULT_TEMPLATE,
        )
        self.assertIn("月度评测", md)
        self.assertIn("100.0%", md)


class TestAiAssess(unittest.TestCase):
    def test_format_markdown(self):
        from qr import ai_assess

        md = ai_assess.format_markdown(
            {
                "generated_at": "2026-06-08 12:00",
                "cursor_month_hours": 10,
                "cursor_month_sessions": 3,
                "screen_month_hours": 40,
                "cursor_by_project": {"dev/qr": 5},
                "decision_notes": 2,
                "prompt_guides": 1,
                "prompt_fragments": 2,
                "chunks": 100,
                "events_total": 200,
                "workspace_projects": ["dev/qr"],
            }
        )
        self.assertIn("AI 使用水平", md)
        self.assertIn("dev/qr", md)


if __name__ == "__main__":
    unittest.main()
