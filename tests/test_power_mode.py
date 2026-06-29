"""AI 服务开关（省电模式）。"""
from __future__ import annotations

import unittest
from unittest import mock

from qr import config, power_mode, tracker


class TestPowerMode(unittest.TestCase):
    def setUp(self):
        self._cfg = config.load_config().copy()

    def tearDown(self):
        config.save_config(self._cfg)

    def test_default_full(self):
        cfg = config.load_config()
        cfg.pop("power_mode", None)
        config.save_config(cfg)
        self.assertFalse(power_mode.is_lite())
        self.assertTrue(power_mode.is_ai_enabled())

    def test_set_lite_pauses_tracker(self):
        tracker.set_pause("off")
        with mock.patch.object(power_mode, "stop_ollama_models", return_value={"stopped": [], "errors": []}):
            out = power_mode.set_mode(power_mode.MODE_LITE)
        self.assertEqual(out["mode"], power_mode.MODE_LITE)
        self.assertFalse(out["ai_enabled"])
        self.assertTrue(tracker.is_tracking_paused())

    def test_set_full_resumes_tracker(self):
        with mock.patch.object(power_mode, "stop_ollama_models", return_value={"stopped": [], "errors": []}):
            power_mode.set_mode(power_mode.MODE_LITE)
        out = power_mode.set_mode(power_mode.MODE_FULL)
        self.assertTrue(out["ai_enabled"])
        self.assertFalse(tracker.is_tracking_paused())

    def test_toggle(self):
        cfg = config.load_config()
        cfg["power_mode"] = power_mode.MODE_FULL
        config.save_config(cfg)
        with mock.patch.object(power_mode, "stop_ollama_models", return_value={"stopped": [], "errors": []}):
            first = power_mode.toggle()
            second = power_mode.toggle()
        self.assertFalse(first["ai_enabled"])
        self.assertTrue(second["ai_enabled"])

    def test_manual_tracker_pause_survives_full_if_not_lite(self):
        tracker.set_pause("2h")
        out = power_mode.set_mode(power_mode.MODE_FULL)
        self.assertTrue(out["ai_enabled"])
        self.assertTrue(tracker.is_tracking_paused())


if __name__ == "__main__":
    unittest.main()
