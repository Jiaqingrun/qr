"""desktop_shell 单元测试。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from qr import desktop_shell


class TestDesktopShell(unittest.TestCase):
    @patch("qr.desktop_shell.service_watch.web_healthy", return_value=True)
    def test_ensure_web_already_running(self, _healthy):
        out = desktop_shell.ensure_web_running()
        self.assertTrue(out["ok"])
        self.assertFalse(out["started"])

    @patch("qr.desktop_shell.subprocess.Popen")
    @patch("qr.desktop_shell.service_watch.restart_web_service", return_value=False)
    @patch("qr.desktop_shell.service_watch.web_healthy")
    def test_ensure_web_spawns_background(self, healthy, _restart, popen):
        healthy.side_effect = [False, False, True]
        out = desktop_shell.ensure_web_running(timeout=2.0)
        self.assertTrue(out["ok"])
        self.assertTrue(out["started"])
        popen.assert_called_once()

    @patch("qr.desktop_shell._alert_macos")
    @patch("qr.desktop_shell.ensure_web_running", return_value={"ok": False, "url": "http://127.0.0.1:8765/"})
    def test_open_native_window_exits_when_web_down(self, _ensure, _alert):
        with self.assertRaises(SystemExit):
            desktop_shell.open_native_window()


if __name__ == "__main__":
    unittest.main()
