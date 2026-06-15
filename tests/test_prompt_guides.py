import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from qr import config, db, prompt_guides


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

    def test_sync_skips_non_execute_session_title(self):
        from unittest import mock

        from qr import db, prompt_guides

        db.init_db()
        sid = f"title-filter-{uuid.uuid4().hex[:8]}"
        with db.session() as conn:
            conn.execute(
                "INSERT INTO events(uid,ts,source,project,title,content,meta) "
                "VALUES(?, ?, 'cursor', 'qr', 'q1', '真实提问', '{}')",
                (f"cursor:{sid}:q0", 1),
            )
            conn.commit()
            with mock.patch(
                "qr.prompt_guides.cst.load_session_titles",
                return_value={sid: "参考-测试对话"},
            ):
                prompt_guides.sync_cursor_inbox(conn)
            row = conn.execute(
                "SELECT 1 FROM prompt_guide_fragments WHERE event_uid=?",
                (f"cursor:{sid}:q0",),
            ).fetchone()
            self.assertIsNone(row)

    def test_sync_includes_execute_session_title(self):
        from unittest import mock

        from qr import db, prompt_guides

        db.init_db()
        sid = f"exec-filter-{uuid.uuid4().hex[:8]}"
        with db.session() as conn:
            conn.execute(
                "INSERT INTO events(uid,ts,source,project,title,content,meta) "
                "VALUES(?, ?, 'cursor', 'qr', '部署脚本', '请写部署脚本', '{}')",
                (f"cursor:{sid}:q0", 1),
            )
            conn.commit()
            with mock.patch(
                "qr.prompt_guides.cst.load_session_titles",
                return_value={sid: "执行-测试任务"},
            ):
                stats = prompt_guides.sync_cursor_inbox(conn)
            self.assertEqual(stats.get("new"), 1)
            row = conn.execute(
                "SELECT content FROM prompt_guide_fragments WHERE event_uid=?",
                (f"cursor:{sid}:q0",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["content"], "请写部署脚本")

    def test_purge_non_execute_keeps_execute_guide(self):
        exec_sid = f"exec-{uuid.uuid4().hex[:8]}"
        ref_sid = f"ref-{uuid.uuid4().hex[:8]}"
        titles = {
            exec_sid: "执行-保留任务",
            ref_sid: "参考-应删除",
        }
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / ".qr"
            home.mkdir()
            db_path = home / "qr.db"
            with mock.patch.object(config, "QR_HOME", home), mock.patch.object(
                config, "DB_PATH", db_path
            ):
                db.init_db()
                with db.session() as conn:
                    prompt_guides.ensure_schema(conn)
                    now = 1
                    conn.execute(
                        "INSERT INTO prompt_guide_types"
                        "(name,slug,description,type_origin,created_at,updated_at) "
                        "VALUES('t','t','', 'auto', ?, ?)",
                        (now, now),
                    )
                    tid = conn.execute(
                        "SELECT id FROM prompt_guide_types",
                    ).fetchone()["id"]
                    conn.execute(
                        "INSERT INTO prompt_guides"
                        "(id,title,body,type_id,origin,project,tags,meta,created_at,updated_at) "
                        "VALUES(9001,'执行引导','body',?,'merged','qr','[]','{}',?,?)",
                        (tid, now, now),
                    )
                    conn.execute(
                        "INSERT INTO prompt_guides"
                        "(id,title,body,type_id,origin,project,tags,meta,created_at,updated_at) "
                        "VALUES(9002,'参考引导','body',?,'merged','qr','[]','{}',?,?)",
                        (tid, now, now),
                    )
                    conn.execute(
                        "INSERT INTO prompt_guide_fragments"
                        "(event_uid,content,project,type_id,type_origin,fragment_origin,"
                        "guide_id,classify_note,ts,ts_estimated,cursor_session_id,"
                        "query_index,transcript_mtime,created_at) "
                        "VALUES('cursor:x:q0','a','qr',?, 'auto','auto',9001,'{}',1,0,?,0,NULL,?)",
                        (tid, exec_sid, now),
                    )
                    conn.execute(
                        "INSERT INTO prompt_guide_fragments"
                        "(event_uid,content,project,type_id,type_origin,fragment_origin,"
                        "guide_id,classify_note,ts,ts_estimated,cursor_session_id,"
                        "query_index,transcript_mtime,created_at) "
                        "VALUES('cursor:y:q0','b','qr',?, 'auto','auto',9002,'{}',1,0,?,0,NULL,?)",
                        (tid, ref_sid, now),
                    )
                    conn.execute(
                        "INSERT INTO prompt_guide_fragments"
                        "(event_uid,content,project,type_id,type_origin,fragment_origin,"
                        "guide_id,classify_note,ts,ts_estimated,cursor_session_id,"
                        "query_index,transcript_mtime,created_at) "
                        "VALUES('cursor:z:q0','c','qr',?, 'auto','auto',NULL,'{}',1,0,?,0,NULL,?)",
                        (tid, ref_sid, now),
                    )
                    conn.commit()
                    with mock.patch(
                        "qr.prompt_guides.cst.load_session_titles",
                        return_value=titles,
                    ):
                        r = prompt_guides.purge_non_execute_prompts(conn, dry_run=False)
                    self.assertEqual(r["guides_removed"], 1)
                    self.assertGreaterEqual(r["fragments_removed"], 2)
                    self.assertIsNotNone(
                        conn.execute(
                            "SELECT 1 FROM prompt_guides WHERE id=9001",
                        ).fetchone(),
                    )
                    self.assertIsNone(
                        conn.execute(
                            "SELECT 1 FROM prompt_guides WHERE id=9002",
                        ).fetchone(),
                    )

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
