"""进化计划自动同步测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import evolution_plan


class TestEvolutionPlan(unittest.TestCase):
    def test_render_and_sync_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / "EVOLUTION_PLAN.md"
            state = Path(td) / "state.json"
            plan.write_text("# old\n", encoding="utf-8")
            fake_rows = [
                {
                    "id": "sports_cursor",
                    "num": 4,
                    "title": "project-sports 真实 Cursor 事件",
                    "acceptance": "test",
                    "status": "done",
                    "status_label": "已完成",
                    "passed": True,
                    "detail": "91 条",
                }
            ]
            with mock.patch.object(evolution_plan, "PLAN_PATH", plan), mock.patch.object(
                evolution_plan, "STATE_PATH", state
            ), mock.patch.object(evolution_plan, "evaluate", return_value=fake_rows), mock.patch.object(
                evolution_plan, "parse_plan_statuses", return_value={"sports_cursor": "active"}
            ):
                res = evolution_plan.sync(quick=True, dry_run=True)
            self.assertTrue(res["ok"])
            self.assertEqual(res["promoted"], ["project-sports 真实 Cursor 事件"])

    def test_cross_project_check(self):
        ok, detail = evolution_plan._check_cross_project(True, {})
        self.assertTrue(ok)
        self.assertIn("跨项目", detail)

    def test_sports_cursor_check(self):
        with mock.patch.object(
            evolution_plan, "_cursor_count_for_sports", return_value=10
        ), mock.patch.object(evolution_plan.config, "load_config", return_value={"evolution_sports_cursor_min": 5}):
            ok, detail = evolution_plan._check_sports_cursor(True, {})
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
