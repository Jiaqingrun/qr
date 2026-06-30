"""规范沿革与治理辅助逻辑（stdlib unittest，无额外依赖）。"""
from __future__ import annotations

import unittest

from qr import standards_changelog


class TestStandardsChangelog(unittest.TestCase):
    def test_skip_test_notes(self) -> None:
        self.assertTrue(standards_changelog.skip_changelog_note("测试v2"))
        self.assertTrue(standards_changelog.skip_changelog_note("debug tweak"))
        self.assertFalse(standards_changelog.skip_changelog_note("根据行为生成新版"))

    def test_prune_noise_versions_import_safe(self) -> None:
        from qr import governance

        # 不应因 standards_changelog 未导入而 NameError
        n = governance.prune_noise_versions()
        self.assertIsInstance(n, int)

    def test_diff_line_add_delete(self) -> None:
        diff = standards_changelog.diff_text("a\nb", "a\nc")
        self.assertIn("c", diff["added"])
        self.assertIn("b", diff["deleted"])
        self.assertFalse(diff["modified"])

    def test_no_substantive_on_whitespace(self) -> None:
        diff = standards_changelog.diff_text("line one\n", "line one\n\n")
        self.assertFalse(standards_changelog.has_substantive_change(diff))

    def test_substantive_on_real_change(self) -> None:
        diff = standards_changelog.diff_text("old rule", "new rule")
        self.assertTrue(standards_changelog.has_substantive_change(diff))

    def test_diff_one_line_to_many_no_recursion(self) -> None:
        old = "\n".join(["prefix"] + ["same"] * 54 + ["- old bullet"])
        new = "\n".join(["prefix"] + ["same"] * 54 + ["- new a", "- new b", "- new c"])
        diff = standards_changelog.diff_text(old, new)
        self.assertIn("- old bullet", diff["deleted"])
        self.assertIn("- new a", diff["added"])

    def test_build_changelog_no_crash(self) -> None:
        out = standards_changelog.build_changelog()
        self.assertIn("changes", out)
        self.assertIn("version_count", out)


if __name__ == "__main__":
    unittest.main()
