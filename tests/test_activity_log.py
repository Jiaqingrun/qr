"""M5-1 非 Cursor 行为通道（activity 类 note）。"""
from __future__ import annotations

import json
import sqlite3
import unittest
from unittest import mock

from qr.collectors import notes
from qr import project_panel


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, uid TEXT UNIQUE, ts INTEGER, "
        "source TEXT, project TEXT, title TEXT, content TEXT, meta TEXT);"
        "CREATE TABLE chat_sessions (id INTEGER PRIMARY KEY, title TEXT);"
        "CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT);"
    )
    return conn


class TestActivityLog(unittest.TestCase):
    def test_add_note_activity_prefix_and_uid(self):
        conn = _conn()
        with mock.patch("qr.collectors.notes.db.now", return_value=1_700_000_000):
            with mock.patch(
                "qr.workspace.canonical_project_id",
                return_value="dev/scribe",
            ):
                notes.add_note(
                    conn,
                    "今天在 scribe 写了 2h 章节草稿",
                    kind="activity",
                    project="dev/scribe",
                )
        row = conn.execute(
            "SELECT uid, title, content, meta, project FROM events WHERE source='note'",
        ).fetchone()
        self.assertTrue(row["uid"].startswith("note:activity:"))
        self.assertTrue(row["title"].startswith("[活动]"))
        self.assertTrue(row["content"].startswith("[活动]"))
        meta = json.loads(row["meta"])
        self.assertEqual(meta["kind"], "activity")
        self.assertEqual(row["project"], "dev/scribe")

    def test_add_note_activity_keeps_existing_prefix(self):
        conn = _conn()
        with mock.patch("qr.collectors.notes.db.now", return_value=1_700_000_000):
            notes.add_note(conn, "[活动] 调研竞品 1h", kind="activity")
        row = conn.execute("SELECT title, content FROM events").fetchone()
        self.assertEqual(row["title"], "[活动] 调研竞品 1h")
        self.assertEqual(row["content"], "[活动] 调研竞品 1h")

    def test_manual_note_timeline_sql_includes_activity(self):
        sql = notes.manual_note_timeline_sql()
        self.assertIn("note:activity:*", sql)
        self.assertIn("'activity'", sql)

    def test_project_panel_counts_activity_notes(self):
        conn = _conn()
        now = 1_700_000_000
        conn.execute(
            "INSERT INTO events(uid,ts,source,project,title,content,meta) VALUES "
            "(?,?,?,?,?,?,?)",
            (
                "note:activity:abc",
                now,
                "note",
                "dev/scribe",
                "[活动] 写稿 2h",
                "[活动] 写稿 2h",
                json.dumps({"kind": "activity"}),
            ),
        )
        conn.commit()
        with mock.patch("qr.project_panel.db.session") as sess:
            sess.return_value.__enter__.return_value = conn
            with mock.patch("qr.project_panel.db.now", return_value=now + 100):
                with mock.patch("qr.project_panel.compliance.scan_index_roots", return_value=[]):
                    with mock.patch(
                        "qr.project_panel.workspace.resolve_project_dir",
                        return_value=None,
                    ):
                        with mock.patch("qr.project_panel.facts.list_facts", return_value=[]):
                            with mock.patch("qr.project_panel.query.search", return_value=[]):
                                data = project_panel.panel("dev/scribe", days=30)
        self.assertEqual(data["activity_notes"], 1)


if __name__ == "__main__":
    unittest.main()
