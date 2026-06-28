"""M10-1 屏幕采样暂停与排除。"""
from __future__ import annotations

import unittest

from qr import config, tracker


class TestTrackerPause(unittest.TestCase):
    def setUp(self):
        self._cfg = config.load_config().copy()

    def tearDown(self):
        config.save_config(self._cfg)

    def test_parse_duration(self):
        self.assertEqual(tracker.parse_pause_duration("2h"), 7200)
        self.assertEqual(tracker.parse_pause_duration("30m"), 1800)
        self.assertEqual(tracker.parse_pause_duration("off"), 0)

    def test_set_pause_and_resume(self):
        r = tracker.set_pause("1h")
        self.assertTrue(r["paused"])
        self.assertTrue(tracker.is_tracking_paused())
        r2 = tracker.set_pause("off")
        self.assertFalse(r2["paused"])
        self.assertFalse(tracker.is_tracking_paused())

    def test_should_record_when_paused(self):
        tracker.set_pause("2h")
        self.assertFalse(tracker.should_record_app("Safari", "com.apple.Safari"))

    def test_exclude_bundle(self):
        tracker.set_pause("off")
        cfg = config.load_config()
        cfg["tracker_exclude_bundles"] = ["com.netflix"]
        config.save_config(cfg)
        self.assertFalse(tracker.should_record_app("Netflix", "com.netflix.Netflix"))
        self.assertTrue(tracker.should_record_app("Cursor", "com.todesktop.cursor"))


if __name__ == "__main__":
    unittest.main()
