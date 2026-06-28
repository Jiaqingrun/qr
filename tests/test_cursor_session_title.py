import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import config, cursor_session_title as cst, db


class TestCursorSessionTitle(unittest.TestCase):
    def test_should_include_execute_only(self):
        self.assertTrue(cst.should_include_in_prompt_guides("执行-QR 知识库 · 工作区分离"))
        self.assertFalse(cst.should_include_in_prompt_guides("参考-QR本地知识库"))
        self.assertFalse(cst.should_include_in_prompt_guides("参考-AI使用水平评估"))
        self.assertFalse(cst.should_include_in_prompt_guides("Stable Diffusion XL machine capabilities"))
        self.assertFalse(cst.should_include_in_prompt_guides("草稿-未登记前缀"))

    def test_session_title_policy(self):
        self.assertEqual(cst.session_title_policy("执行-太一"), "execute")
        self.assertEqual(cst.session_title_policy("参考-远程连接树莓派"), "reference")
        self.assertEqual(cst.session_title_policy("无连字符标题"), "pending")
        self.assertEqual(cst.session_title_policy("未来-新前缀"), "unknown_prefix")

    def test_parse_session_prefix(self):
        self.assertEqual(cst.parse_session_prefix("执行-Pdf 页面统计工具"), "执行")
        self.assertIsNone(cst.parse_session_prefix("还没改标题"))

    def test_prefix_meta_for_listable_pending(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "QR"
            (root / "dev" / "qr").mkdir(parents=True)
            cfg = {"workspace_root": str(root), "project_categories": ["dev"]}
            meta = cst.prefix_meta_for_chat("还没改标题", "dev/qr", cfg=cfg)
            self.assertTrue(meta.get("prompt_prefix_pending"))
            self.assertIn("执行", meta.get("prompt_prefix_hint", ""))

    def test_refresh_and_count_pending(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / ".qr"
            home.mkdir()
            db_path = home / "qr.db"
            root = Path(td) / "QR"
            (root / "dev" / "qr").mkdir(parents=True)
            cfg = {"workspace_root": str(root), "project_categories": ["dev"]}
            sid = "sess-pending-abc"
            now = db.now()
            with mock.patch.object(config, "QR_HOME", home), mock.patch.object(
                config, "DB_PATH", db_path
            ):
                db.init_db()
                with db.session() as conn:
                    meta = json.dumps({"session_id": sid}, ensure_ascii=False)
                    conn.execute(
                        "INSERT INTO events(uid,ts,source,project,title,content,meta) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (f"cursor:{sid}:q0", now, "cursor", "dev/qr", "q", "body", meta),
                    )
                    conn.commit()
                    with mock.patch(
                        "qr.cursor_session_title.load_session_titles",
                        return_value={sid: "普通对话标题"},
                    ):
                        rep = cst.refresh_prefix_annotations(conn, cfg=cfg)
                        cnt = cst.count_pending_prefix_sessions(conn, days=30, cfg=cfg)
                    row = conn.execute(
                        "SELECT meta FROM events WHERE uid=?",
                        (f"cursor:{sid}:q0",),
                    ).fetchone()
            self.assertEqual(rep["pending_sessions"], 1)
            self.assertEqual(cnt["sessions"], 1)
            meta_obj = json.loads(row["meta"])
            self.assertTrue(meta_obj.get("prompt_prefix_pending"))


if __name__ == "__main__":
    unittest.main()
