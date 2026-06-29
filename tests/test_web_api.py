"""Web API 与运维面板集成测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from qr import config, db, ops_panel
from qr.web import app


class TestWebApi(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_api_status(self):
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("documents", data)
        self.assertIn("pillars", data)

    def test_api_query_validation(self):
        r = self.client.post("/api/query", json={"q": ""})
        self.assertIn(r.status_code, (200, 422, 400))

    def test_api_query_with_question(self):
        with mock.patch("qr.web.query.search", return_value=[]):
            r = self.client.post("/api/query", json={"text": "QR 知识库是什么", "k": 3})
        self.assertEqual(r.status_code, 200)
        self.assertIn("hits", r.json())

    def test_api_ops_overview(self):
        r = self.client.get("/api/ops/overview")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("doctor", data)
        self.assertIn("schedule", data)
        self.assertIn("config", data)

    def test_api_ops_import_discover(self):
        with mock.patch("qr.ops_panel.importer.discover", return_value=[]):
            r = self.client.get("/api/ops/import/discover")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["projects"], [])

    def test_api_ops_schedule_install(self):
        with mock.patch(
            "qr.ops_panel.install_schedule",
            return_value={"ok": True, "schedule": {"loaded": 6, "total": 7}, "installed": []},
        ), mock.patch("qr.web._ops_install_web_agents_background"):
            r = self.client.post("/api/ops/schedule/install")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("web_restart_pending"))
        self.assertIn("message", data)

    def test_api_ops_schedule_uninstall(self):
        with mock.patch(
            "qr.ops_panel.uninstall_schedule_agent",
            return_value={
                "ok": True,
                "label": "com.qr.eval",
                "title": "每月模型评测",
                "schedule": {"loaded": 7, "total": 8},
            },
        ):
            r = self.client.post(
                "/api/ops/schedule/uninstall",
                json={"label": "com.qr.eval"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["label"], "com.qr.eval")

    def test_api_ops_schedule_uninstall_unknown(self):
        r = self.client.post(
            "/api/ops/schedule/uninstall",
            json={"label": "com.qr.unknown"},
        )
        self.assertEqual(r.status_code, 400)

    def test_api_power_status(self):
        r = self.client.get("/api/power/status")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("ai_enabled", data)
        self.assertIn("hint", data)

    def test_api_power_set(self):
        with mock.patch("qr.power_mode.set_enabled") as set_enabled:
            set_enabled.return_value = {
                "mode": "lite",
                "ai_enabled": False,
                "hint": "关 · 已停 AI",
                "message": "AI 服务已关闭（省电）",
            }
            r = self.client.post("/api/power", json={"enabled": False})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["ai_enabled"])
        set_enabled.assert_called_once_with(False)

    def test_api_query_blocked_when_lite(self):
        with mock.patch("qr.power_mode.is_lite", return_value=True):
            r = self.client.post("/api/query", json={"text": "test", "k": 3})
        self.assertEqual(r.status_code, 503)
        self.assertIn("AI 服务已关闭", r.json()["error"])

    def test_api_console_events(self):
        with mock.patch(
            "qr.web.console_log.tail",
            return_value=[{"ts": 1, "source": "web", "kind": "stdout", "text": "hi"}],
        ):
            r = self.client.get("/api/console/events?limit=10")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["events"][0]["text"], "hi")

    def test_api_console_jobs(self):
        with mock.patch(
            "qr.web.console_log.active_jobs",
            return_value=[{"job_id": "x", "label": "索引", "source": "web"}],
        ):
            r = self.client.get("/api/console/jobs")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["jobs"][0]["job_id"], "x")

    def test_api_console_agents(self):
        with mock.patch(
            "qr.web.console_log.agent_log_files",
            return_value=[{"label": "com.qr.web", "name": "web", "title": "Web 服务"}],
        ):
            r = self.client.get("/api/console/agents")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["agents"][0]["name"], "web")

    def test_sync_git_scan_roots(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.json"
            cfg = dict(config.DEFAULT_CONFIG)
            cfg["index_roots"] = ["~/QR", "~/Projects/demo"]
            cfg["git_scan_roots"] = ["~/QR"]
            cfg_path.write_text(
                __import__("json").dumps(cfg, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with mock.patch.object(config, "CONFIG_PATH", cfg_path), mock.patch.object(
                config, "QR_HOME", Path(td) / ".qr"
            ):
                result = ops_panel.sync_git_scan_roots()
            self.assertTrue(result["changed"])
            self.assertIn("~/Projects/demo", result["added"])


class TestOpsPanel(unittest.TestCase):
    def test_list_backups_empty(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(config, "QR_HOME", Path(td)):
                self.assertEqual(ops_panel.list_backups(), [])

    def test_run_backup(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            db_path = home / "qr.db"
            db.init_db()
            with mock.patch.object(config, "QR_HOME", home), mock.patch.object(
                config, "DB_PATH", db_path
            ):
                config.ensure_dirs()
                result = ops_panel.run_backup()
            self.assertTrue(Path(result["path"]).exists())


if __name__ == "__main__":
    unittest.main()
