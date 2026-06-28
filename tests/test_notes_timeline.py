"""时间线 note 仅保留手动记录。"""
from __future__ import annotations

import json
import sqlite3
import unittest

from qr.collectors import notes


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE events (id INTEGER PRIMARY KEY, uid TEXT UNIQUE, ts INTEGER, "
        "source TEXT, project TEXT, title TEXT, content TEXT, meta TEXT);"
    )
    return conn


class TestNotesTimeline(unittest.TestCase):
    def test_is_manual_timeline_note(self):
        self.assertTrue(
            notes.is_manual_timeline_note("note:note:abc", '{"kind":"note"}'),
        )
        self.assertTrue(
            notes.is_manual_timeline_note("note:decision:abc", '{"kind":"decision"}'),
        )
        self.assertTrue(
            notes.is_manual_timeline_note("note:activity:abc", '{"kind":"activity"}'),
        )
        self.assertFalse(
            notes.is_manual_timeline_note("note:file:abc", '{"kind":"file"}'),
        )
        self.assertFalse(
            notes.is_manual_timeline_note("note:legacy", '{"kind":"file","path":"/x"}'),
        )

    def test_collect_purges_file_notes(self):
        conn = _conn()
        conn.execute(
            "INSERT INTO events(uid,ts,source,title,content,meta) VALUES "
            "('note:file:1', 1, 'note', 'file note', 'body', ?)",
            (json.dumps({"kind": "file", "path": "/tmp/x.md"}),),
        )
        conn.execute(
            "INSERT INTO events(uid,ts,source,title,content,meta) VALUES "
            "('note:note:2', 2, 'note', 'manual', 'body', ?)",
            (json.dumps({"kind": "note"}),),
        )
        conn.commit()
        removed = notes.collect(conn)
        self.assertEqual(removed, 1)
        left = conn.execute("SELECT uid FROM events WHERE source='note'").fetchall()
        self.assertEqual([r["uid"] for r in left], ["note:note:2"])


if __name__ == "__main__":
    unittest.main()
