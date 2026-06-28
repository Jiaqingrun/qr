"""短板修复 P0 模块单元测试。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import decision_draft, health, prompt_guides, ship_check, workspace


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, uid TEXT, ts INTEGER, source TEXT, "
        "project TEXT, title TEXT, content TEXT, meta TEXT);"
        "CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT);"
    )
    return conn


class TestShipCheck(unittest.TestCase):
    def test_resolve_project_from_repo(self):
        with mock.patch.object(workspace, "workspace_root", return_value=Path("/QR")):
            pid, pdir = ship_check.resolve_project(
                project="dev/qr",
                cwd=Path("/QR/dev/qr"),
            )
        self.assertEqual(pid, "dev/qr")

    def test_exit_code_doctor_error(self):
        result = {
            "ok": False,
            "steps": [{"id": "doctor", "ok": False}],
        }
        self.assertEqual(ship_check.exit_code(result), 1)


class TestCursorWorkspaceAudit(unittest.TestCase):
    def test_flags_window_project(self):
        conn = _mem_conn()
        now = 1_700_000_000
        conn.execute(
            "INSERT INTO events(uid,ts,source,project,title) VALUES(?,?,?,?,?)",
            ("c1", now, "cursor", "window", "q"),
        )
        conn.commit()
        with mock.patch("qr.health.db.now", return_value=now + 100):
            rep = health.audit_cursor_workspace(conn, days=30)
        self.assertFalse(rep["ok"])
        self.assertTrue(any(x["project"] == "window" for x in rep["suspicious"]))

    def test_ok_for_listable_project(self):
        conn = _mem_conn()
        now = 1_700_000_000
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "QR"
            proj = root / "dev" / "qr"
            proj.mkdir(parents=True)
            cfg = {
                "workspace_root": str(root),
                "project_categories": ["dev"],
            }
            conn.execute(
                "INSERT INTO events(uid,ts,source,project,title) VALUES(?,?,?,?,?)",
                ("c1", now, "cursor", "dev/qr", "q"),
            )
            conn.commit()
            with mock.patch("qr.health.db.now", return_value=now + 100):
                rep = health.audit_cursor_workspace(conn, days=30, cfg=cfg)
            self.assertTrue(rep["ok"])


class TestSuggestMerge(unittest.TestCase):
    def test_clusters_similar_fragments(self):
        from qr import config, db as qr_db

        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / ".qr"
            home.mkdir()
            db_path = home / "qr.db"
            with mock.patch.object(config, "QR_HOME", home), mock.patch.object(
                config, "DB_PATH", db_path
            ):
                qr_db.init_db()
                with qr_db.session() as conn:
                    prompt_guides.ensure_schema(conn)
                    now = 1_700_000_000
                    for i, text in enumerate([
                        "如何修复 RAG 检索命中率",
                        "怎么提高 RAG 检索命中率",
                        "完全不同的问法主题",
                    ]):
                        conn.execute(
                            "INSERT INTO prompt_guide_fragments("
                            "content,project,ts,created_at,type_origin,fragment_origin,guide_id"
                            ") VALUES(?,?,?,?, 'auto','auto',NULL)",
                            (text, "dev/qr", now + i, now + i),
                        )
                    conn.commit()
                    clusters = prompt_guides.suggest_merge_clusters(
                        conn, threshold=0.5, limit=50,
                    )
        self.assertTrue(clusters)
        ids = set(clusters[0]["fragment_ids"])
        self.assertEqual(len(ids), 2)


class TestDecisionDraft(unittest.TestCase):
    def test_build_draft_from_events(self):
        conn = _mem_conn()
        now = 1_700_000_000
        conn.execute(
            "INSERT INTO events(uid,ts,source,project,title,content) VALUES(?,?,?,?,?,?)",
            ("cursor:abc-uuid:q1", now, "cursor", "dev/qr", "如何补齐短板", "如何补齐短板"),
        )
        conn.commit()
        with mock.patch("qr.decision_draft.db.session") as sess:
            sess.return_value.__enter__ = mock.Mock(return_value=conn)
            sess.return_value.__exit__ = mock.Mock(return_value=False)
            with mock.patch("qr.decision_draft.db.init_db"):
                draft = decision_draft.build_draft(session_id="abc-uuid")
        self.assertIn("决策记录", draft["text"])
        self.assertIn("如何补齐短板", draft["text"])
        self.assertFalse(draft["auto_save"])


class TestFocusProject(unittest.TestCase):
    def test_resume_prefers_config_focus(self):
        from qr import config, db as qr_db, resume_panel

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "QR"
            proj = root / "dev" / "qr"
            proj.mkdir(parents=True)
            (proj / "README.md").write_text("# test\n", encoding="utf-8")
            home = Path(td) / ".qr"
            home.mkdir()
            db_path = home / "qr.db"
            cfg = {
                "workspace_root": str(root),
                "project_categories": ["dev"],
                "focus_project": "dev/qr",
            }
            with mock.patch.object(config, "QR_HOME", home), mock.patch.object(
                config, "DB_PATH", db_path
            ), mock.patch("qr.resume_panel.config.load_config", return_value=cfg):
                qr_db.init_db()
                with qr_db.session() as conn:
                    rep = resume_panel.generate(conn)
            self.assertEqual(rep.get("focus_project"), "dev/qr")
            self.assertTrue(rep.get("focus_from_config"))
            self.assertEqual(rep.get("active_project"), "dev/qr")


if __name__ == "__main__":
    unittest.main()
