"""M9-3 核心路径测试补强。"""
from __future__ import annotations

import unittest
from unittest import mock

from qr import decision_draft, usage, workspace
from qr import mcp_server


class TestCorePaths(unittest.TestCase):
    def test_usage_paradox_launcher_excluded(self):
        self.assertTrue(
            usage.is_excluded_usage("Paradox Launcher", "com.paradoxplaza.launcher")
        )

    def test_decision_draft_template_sections(self):
        with mock.patch("qr.decision_draft.db.session") as sess:
            conn = mock.MagicMock()
            sess.return_value.__enter__.return_value = conn
            conn.execute.return_value.fetchall.return_value = []
            conn.execute.return_value.fetchone.return_value = None
            with mock.patch("qr.decision_draft.db.init_db"):
                draft = decision_draft.build_draft(project="dev/qr")
        text = draft["text"]
        for sec in ("## 问题", "## 选项", "## 结论", "## 原因"):
            self.assertIn(sec, text)

    def test_cursor_project_from_slug(self):
        self.assertEqual(
            workspace.project_from_cursor_dir_name("Users-qr-QR-dev-qr"),
            "dev/qr",
        )

    def test_mcp_qr_search_mock(self):
        fake_hits = [{"path": "/a.py", "text": "hello", "score": 0.9}]
        with mock.patch("qr.query.search", return_value=fake_hits):
            out = mcp_server._call_tool("qr_search", {"question": "test", "k": 3})
        body = out["content"][0]["text"]
        self.assertIn("/a.py", body)


if __name__ == "__main__":
    unittest.main()
