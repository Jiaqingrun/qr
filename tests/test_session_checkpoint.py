"""M3-3 长会话 checkpoint。"""
from __future__ import annotations

import json
import sqlite3
import unittest
from unittest import mock

from qr import session_checkpoint


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE events (uid TEXT PRIMARY KEY, ts INTEGER, source TEXT, "
        "project TEXT, title TEXT, content TEXT, meta TEXT);"
        "CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE VIRTUAL TABLE events_fts USING fts5(uid, source, project, title, content);"
    )
    return conn


class TestSessionCheckpoint(unittest.TestCase):
    def test_parse_cursor_uid(self):
        self.assertEqual(
            session_checkpoint.parse_cursor_event_uid("cursor:abc-uuid:q12"),
            ("abc-uuid", 12),
        )
        self.assertIsNone(session_checkpoint.parse_cursor_event_uid("note:x"))

    def test_turn_counts(self):
        conn = _conn()
        for i in range(45):
            conn.execute(
                "INSERT INTO events(uid,ts,source,project,title) VALUES(?,?,?,?,?)",
                (f"cursor:sess1:q{i}", i, "cursor", "dev/qr", f"q{i}"),
            )
        conn.commit()
        counts = session_checkpoint.turn_counts(conn, ["sess1"])
        self.assertEqual(counts["sess1"], 45)

    def test_extractive_checkpoint(self):
        turns = [
            {"query": "完成 M3-2 注册表", "reply": "ok", "ts": 1, "query_index": 0},
            {"query": "继续 M3-3 checkpoint", "reply": "", "ts": 2, "query_index": 1},
        ]
        body = session_checkpoint._extractive_body("sid-1", "dev/qr", turns)
        self.assertIn("## 已完成", body)
        self.assertIn("## 待办", body)
        self.assertIn("## 风险", body)

    def test_create_checkpoint_insufficient_turns(self):
        conn = _conn()
        conn.execute(
            "INSERT INTO events(uid,ts,source,project,title) VALUES(?,?,?,?,?)",
            ("cursor:s:q0", 1, "cursor", "dev/qr", "hi"),
        )
        conn.commit()
        with self.assertRaises(ValueError):
            session_checkpoint.create_checkpoint(conn, "s")

    def test_create_checkpoint_extractive(self):
        conn = _conn()
        for i in range(42):
            conn.execute(
                "INSERT INTO events(uid,ts,source,project,title,meta) VALUES(?,?,?,?,?,?)",
                (
                    f"cursor:long-s:q{i}",
                    i,
                    "cursor",
                    "dev/qr",
                    f"question {i}",
                    json.dumps({"session_id": "long-s"}),
                ),
            )
        conn.commit()
        with mock.patch.object(session_checkpoint, "generate_body", return_value="# 会话 Checkpoint\n\n## 已完成\n\n- x\n\n## 待办\n\n- y\n\n## 风险\n\n- z\n"):
            with mock.patch("qr.session_checkpoint.timeline_search.index_event"):
                result = session_checkpoint.create_checkpoint(conn, "long-s")
        self.assertTrue(result["created"])
        self.assertIn("note:checkpoint:", result["uid"])
        row = conn.execute(
            "SELECT title, meta FROM events WHERE uid=?",
            (result["uid"],),
        ).fetchone()
        self.assertIn("Checkpoint", row["title"])
        meta = json.loads(row["meta"])
        self.assertEqual(meta["kind"], "checkpoint")

    def test_enrich_timeline_items(self):
        conn = _conn()
        for i in range(41):
            conn.execute(
                "INSERT INTO events(uid,ts,source,project,title) VALUES(?,?,?,?,?)",
                (f"cursor:es:q{i}", i, "cursor", "dev/qr", f"q{i}"),
            )
        conn.commit()
        items = [
            {"uid": "cursor:es:q40", "source": "cursor", "title": "last"},
            {"uid": "other:1", "source": "note", "title": "n"},
        ]
        session_checkpoint.enrich_timeline_items(conn, items)
        self.assertTrue(items[0].get("show_checkpoint_btn"))
        self.assertEqual(items[0].get("session_turns"), 41)


if __name__ == "__main__":
    unittest.main()
