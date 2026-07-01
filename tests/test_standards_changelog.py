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

    def test_diff_large_replace_no_recursion(self) -> None:
        """v15→v16 等大段改写曾触发 _append_line_diff 无限递归。"""
        old_lines = [f"- 旧条目 {i}" for i in range(80)]
        new_lines = [f"- 新条目 {i}" for i in range(80)]
        diff = standards_changelog.diff_text("\n".join(old_lines), "\n".join(new_lines))
        self.assertTrue(standards_changelog.has_substantive_change(diff))

    def test_build_changelog_live_db(self) -> None:
        from qr import governance

        governance.ensure_standards()
        payload = standards_changelog.build_changelog(prune_identical=False)
        self.assertIn("changes", payload)
        self.assertGreaterEqual(payload["version_count"], 2)


if __name__ == "__main__":
    unittest.main()
