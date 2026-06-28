"""M6-1 规范修订 diff 预览与待确认队列。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import config, governance, standards_revision


def _valid_standards(extra: str = "") -> str:
    base = governance._seed_content()
    if extra:
        return base.rstrip() + "\n\n" + extra + "\n"
    return base


class TestStandardsRevision(unittest.TestCase):
    def test_needs_confirmation_default_true(self):
        self.assertTrue(standards_revision.needs_confirmation({}))
        self.assertTrue(standards_revision.needs_confirmation({"standards_auto_confirm": True}))
        self.assertFalse(standards_revision.needs_confirmation({"standards_auto_confirm": False}))

    def test_section_boundaries(self):
        text = "## 一、存储\n\n## 六、界面\n"
        secs = standards_revision.section_boundaries(text)
        self.assertEqual([s["section"] for s in secs], ["一", "六"])

    def test_diff_preview_small_change(self):
        diff = standards_revision.diff_preview("line a\n", "line a\nline b\n")
        self.assertTrue(diff["has_change"])
        self.assertIn("line b", diff["added"][0])

    def test_pending_store_confirm(self):
        old = _valid_standards()
        new = old + "\n- 待确认测试行\n"
        with tempfile.TemporaryDirectory() as td:
            pending = Path(td) / "pending.json"
            with mock.patch.object(standards_revision, "PENDING_PATH", pending):
                standards_revision.store_pending(
                    before=old,
                    after=new,
                    note="待确认：测试",
                    period="week",
                )
                loaded = standards_revision.load_pending()
                self.assertIsNotNone(loaded)
                self.assertTrue(loaded["diff"]["has_change"])
                with mock.patch.object(config, "STANDARDS_PATH", Path(td) / "standards.md"):
                    Path(td, "standards.md").write_text(old, encoding="utf-8")
                    with mock.patch.object(governance.config, "STANDARDS_PATH", Path(td) / "standards.md"):
                        content, recorded = standards_revision.confirm_pending(note="验收")
                self.assertIn("待确认测试行", content)
                self.assertIsNone(standards_revision.load_pending())

    def test_finish_global_revision_pending(self):
        old = _valid_standards()
        new = old + "\n- 草案\n"
        with tempfile.TemporaryDirectory() as td:
            pending = Path(td) / "pending.json"
            with mock.patch.object(standards_revision, "PENDING_PATH", pending):
                with mock.patch.object(standards_revision, "needs_confirmation", return_value=True):
                    content, saved, changed, pending_flag = standards_revision.finish_global_revision(
                        new,
                        old,
                        "测试修订",
                        confirm=True,
                    )
                self.assertTrue(pending_flag)
                self.assertFalse(saved)
                self.assertTrue(changed)
                self.assertIsNotNone(standards_revision.load_pending())

    def test_propose_rejects_invalid_output(self):
        with mock.patch("qr.summary._window", return_value=(0, 1)):
            with mock.patch("qr.governance.db.session") as sess:
                sess.return_value.__enter__.return_value = mock.Mock()
                with mock.patch(
                    "qr.governance._digest_for_revision",
                    return_value="行为摘要",
                ):
                    with mock.patch("qr.ollama_client.Ollama") as Ollama:
                        Ollama.return_value.generate.return_value = "太短"
                        with self.assertRaises(ValueError):
                            governance.propose_global_revision(
                                "week", from_conversations=False
                            )

    def test_format_cli_diff(self):
        text = standards_revision.format_cli_diff(
            {"unified": ["--- 当前", "+++ 草案", "+new line"]}
        )
        self.assertIn("new line", text)


if __name__ == "__main__":
    unittest.main()
