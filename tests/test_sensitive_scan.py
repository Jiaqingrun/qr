"""M10-2 敏感模式扫描。"""
from __future__ import annotations

import unittest

from qr import sensitive_scan


class TestSensitiveScan(unittest.TestCase):
    def test_fake_akia_detected(self):
        labels = sensitive_scan.scan_text("key = AKIAIOSFODNN7EXAMPLE")
        self.assertTrue(any("AWS" in x for x in labels))

    def test_clean_text(self):
        self.assertEqual(sensitive_scan.scan_text("hello qr"), [])

    def test_meta_patch(self):
        meta = sensitive_scan.meta_patch_for_content("token ghp_" + "a" * 36)
        self.assertTrue(meta.get("sensitive_warning"))
        self.assertTrue(meta.get("sensitive_patterns"))


if __name__ == "__main__":
    unittest.main()
