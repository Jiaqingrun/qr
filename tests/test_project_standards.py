import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import config, governance, ops_timeline, project_standards, workspace


class TestProjectStandards(unittest.TestCase):
    def test_valid_project_template(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            project_standards.ensure_project_standards(d, project_id="dev/demo")
            body = project_standards.read_project_standards(d)
            self.assertIsNotNone(body)
            self.assertIn("## 用途", body or "")

    def test_reject_global_content_in_project_md(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            bad = (
                "# 项目约定 · demo\n\n## 一、存储与目录规范\n\n"
                "## 用途\nx\n\n## 技术栈与结构\n-\n\n"
                "## 开发约定\n-\n\n## AI 协作（本项目）\n-\n"
            )
            with self.assertRaises(ValueError) as ctx:
                project_standards.save_project_standards(
                    d, bad, project_id="dev/demo", note="test"
                )
            self.assertIn("不混写", str(ctx.exception))
            self.assertTrue(project_standards.mixed_standards_issues(bad))

    def test_generate_rules_splits_personal_and_project(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            project_standards.ensure_project_standards(d, project_id="dev/demo")
            (d / "PROJECT.md").write_text(
                "# 项目约定 · demo\n\n## 用途\n测试\n\n## 技术栈与结构\n-\n\n"
                "## 开发约定\n-\n\n## AI 协作（本项目）\n-\n",
                encoding="utf-8",
            )
            with mock.patch.object(governance, "read_standards", return_value="# 个人\n\n## 一、\nx\n## 二、\n## 三、\n## 四、\n## 五、\n## 六、\n"):
                files = governance.generate_rules(d)
            names = {p.name for p in files}
            self.assertIn("00-personal-standards.mdc", names)
            self.assertIn(project_standards.PROJECT_RULE, names)
            agents = (d / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("本项目约定", agents)

    def test_project_from_cursor_dir_name(self):
        self.assertEqual(
            workspace.project_from_cursor_dir_name("Users-qr-QR-dev-qr"),
            "dev/qr",
        )
        self.assertEqual(
            workspace.project_from_cursor_dir_name("Users-qr-QR-dev-project-sports"),
            "dev/sports/project-sports",
        )

    def test_listable_workspace_only(self):
        self.assertFalse(workspace.is_listable_project_id("documents/foo"))
        self.assertFalse(workspace.is_listable_project_id("legacy/documents/bar"))
        self.assertFalse(workspace.is_listable_project_id("Documents/EVE"))
        self.assertFalse(workspace.is_listable_project_id("Zomboid/Logs"))
        self.assertFalse(workspace.is_listable_project_id("forge"))
        self.assertFalse(workspace.is_listable_project_id("cursor-forge"))
        self.assertFalse(workspace.is_listable_project_id("dev/qr-export"))
        if workspace.resolve_project_dir("dev/qr"):
            self.assertTrue(workspace.is_listable_project_id("dev/qr"))

    def test_searchable_content(self):
        home = Path.home()
        self.assertTrue(
            workspace.is_searchable_content(str(home / "QR/dev/qr/README.md"), "dev/qr")
        )
        self.assertTrue(
            workspace.is_searchable_content(str(config.QR_HOME / "standards.md"), "qr-standards")
        )
        self.assertFalse(
            workspace.is_searchable_content(
                str(home / "Documents/EVE/foo.txt"), "Documents/EVE"
            )
        )
        self.assertIsNone(workspace.sanitize_display_project("Documents/EVE"))
        self.assertEqual(workspace.sanitize_display_project("dev/qr"), "dev/qr")
        self.assertEqual(workspace.sanitize_display_project("qr"), "dev/qr")
        self.assertEqual(workspace.project_timeline_label("dev/sports/project-sports"), "project-sports")

    def test_event_row_visible(self):
        self.assertFalse(workspace.event_row_visible("file", "Documents/EVE"))
        self.assertTrue(workspace.event_row_visible("cursor", "forge"))
        self.assertTrue(workspace.event_row_visible("file", "dev/qr"))

    def test_event_timeline_hidden_cursor_ingest(self):
        meta = '{"kind":"qr_operation","action":"ingest.cursor","via":"web"}'
        self.assertTrue(
            workspace.event_timeline_hidden("qr", "[知识库] Cursor 采集", meta)
        )
        self.assertFalse(
            workspace.event_timeline_hidden("qr", "[知识库] 本地问答", '{"action":"ask"}')
        )
        self.assertFalse(workspace.event_timeline_hidden("cursor", "某对话", meta))
        sql = workspace.events_timeline_hidden_sql()
        self.assertIn("ingest.cursor", sql)
        self.assertIn("[知识库] Cursor 采集", sql)
        self.assertIn("NOT", sql)
        self.assertTrue(ops_timeline.skip_timeline_path("/api/ingest/cursor"))
        self.assertFalse(ops_timeline.should_log_http("POST", "/api/ingest/cursor", 200))
        self.assertIsNone(
            ops_timeline.describe_http("POST", "/api/ingest/cursor", {}, {})
        )

    def test_purge_timeline_hidden_qr_events(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE events (
              uid TEXT PRIMARY KEY, ts INTEGER, source TEXT, project TEXT,
              title TEXT, content TEXT, meta TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
            (
                "qr:ingest.cursor:1",
                1,
                "qr",
                None,
                "[知识库] Cursor 采集",
                "sync",
                '{"action":"ingest.cursor"}',
            ),
        )
        conn.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
            ("qr:ask:2", 2, "qr", None, "[知识库] 本地问答", "q", '{"action":"ask"}'),
        )
        conn.commit()
        self.assertEqual(workspace.purge_timeline_hidden_qr_events(conn), 1)
        left = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        self.assertEqual(left, 1)

    def test_list_projects_grouped_no_index_ghosts(self):
        g = workspace.list_projects_grouped()
        for pid in g["projects"]:
            self.assertIn("/", pid)
            self.assertTrue(workspace.is_listable_project_id(pid))
        ghosts = {"Documents/EVE", "Zomboid/Logs", "cursor-forge"}
        self.assertFalse(ghosts & set(g["projects"]))


class TestGovernanceSections(unittest.TestCase):
    def test_upgrade_adds_section_six(self):
        seed = governance._seed_content()
        idx = seed.find("## 六、")
        base = seed[:idx].rstrip() + "\n"
        with tempfile.TemporaryDirectory() as td:
            standards = Path(td) / "standards.md"
            standards.write_text(base, encoding="utf-8")
            with mock.patch.object(governance.config, "STANDARDS_PATH", standards):
                with mock.patch.object(governance, "_seed_content", return_value=seed):
                    with mock.patch.object(
                        governance.db, "session", side_effect=lambda: _NullCtx()
                    ):
                        changed = governance.upgrade_standards_sections()
            self.assertTrue(changed)
            self.assertIn("## 六、", standards.read_text(encoding="utf-8"))


class _NullCtx:
    def __enter__(self):
        return _FakeConn()

    def __exit__(self, *a):
        pass


class _FakeConn:
    def execute(self, *a, **k):
        class R:
            def fetchone(self):
                return None

        return R()


class TestStandardsAuto(unittest.TestCase):
    def test_should_run_respects_interval(self):
        from qr import standards_auto
        import time

        cfg = {"standards_auto_revise": True, "standards_auto_interval_hours": 168}
        with mock.patch.object(standards_auto.db, "session") as sess, mock.patch.object(
            standards_auto.db,
            "get_state",
            return_value=str(int(time.time()) - 100),
        ):
            conn = mock.MagicMock()
            sess.return_value.__enter__.return_value = conn
            self.assertFalse(standards_auto.should_run(cfg, force=False))
            self.assertTrue(standards_auto.should_run(cfg, force=True))


if __name__ == "__main__":
    unittest.main()
