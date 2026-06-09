import unittest
import uuid

from qr import prompt_guides


class PromptGuideQueryTests(unittest.TestCase):
    def test_is_archive_path(self):
        self.assertTrue(
            prompt_guides._is_archive_path(
                "/Users/qr/.qr/cursor_chats/abc/q0001.md",
            ),
        )
        self.assertFalse(prompt_guides._is_archive_path("我在AI使用方面属于什么水平"))

    def test_enrich_fragment_project_label(self):
        frag = prompt_guides._enrich_fragment({"project": "qr", "event_uid": "cursor:x:q1", "ts": 0})
        self.assertEqual(frag["project"], "dev/qr")
        self.assertEqual(frag["project_label"], "qr")

    def test_resolve_prefers_event_title_over_path(self):
        q = prompt_guides._resolve_fragment_query(
            content="/Users/qr/.qr/cursor_chats/x/q0000.md",
            event_title="我在AI使用方面属于什么水平",
            event_uid="cursor:x:q0",
        )
        self.assertEqual(q, "我在AI使用方面属于什么水平")


    def test_shield_event_writes_dismiss_and_removes_event(self):
        from qr import db, prompt_guides

        db.init_db()
        with db.session() as conn:
            uid = f"cursor:test-session-{uuid.uuid4().hex[:8]}:q0"
            conn.execute(
                "INSERT INTO events(uid,ts,source,project,title,content,meta) "
                "VALUES(?, ?, 'cursor', 'qr', 'hello', 'body', '{}')",
                (uid, 1),
            )
            conn.commit()
            n = prompt_guides._shield_event_uids(conn, [uid])
            self.assertEqual(n, 1)
            self.assertTrue(prompt_guides._is_dismissed(conn, uid))
            row = conn.execute("SELECT 1 FROM events WHERE uid=?", (uid,)).fetchone()
            self.assertIsNone(row)

    def test_sync_skips_dismissed(self):
        from qr import db, prompt_guides

        db.init_db()
        with db.session() as conn:
            uid = f"cursor:shield-test-{uuid.uuid4().hex[:8]}:q0"
            prompt_guides._dismiss_events(conn, [uid])
            conn.execute(
                "INSERT INTO events(uid,ts,source,project,title,content,meta) "
                "VALUES(?, ?, 'cursor', 'qr', 'ignored', 'x', '{}')",
                (uid, 1),
            )
            conn.commit()
            prompt_guides.sync_cursor_inbox(conn)
            row = conn.execute(
                "SELECT 1 FROM prompt_guide_fragments WHERE event_uid=?",
                (uid,),
            ).fetchone()
            self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
