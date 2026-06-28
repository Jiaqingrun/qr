"""M5-2 shell 时间戳统计。"""
from __future__ import annotations

import unittest
from unittest import mock

from qr import shell_check


class TestShellTimestamp(unittest.TestCase):
    def test_timestamp_stats_structure(self):
        with mock.patch("qr.collectors.shell._iter_history", return_value=[
            (1_700_000_000, "ls"),
            (None, "old line"),
            (1_700_100_000, "qr doctor"),
        ]):
            st = shell_check.timestamp_stats(days=7)
        self.assertEqual(st["file_total"], 3)
        self.assertEqual(st["file_with_ts"], 2)
        self.assertGreater(st["file_pct"], 0)
        self.assertIn("snippet", st)


if __name__ == "__main__":
    unittest.main()
