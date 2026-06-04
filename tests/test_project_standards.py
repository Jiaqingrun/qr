import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import config, governance, project_standards, workspace


class TestProjectStandards(unittest.TestCase):
    def test_valid_project_template(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            project_standards.ensure_project_standards(d, project_id="dev/demo")
            body = project_standards.read_project_standards(d)
            self.assertIsNotNone(body)
            self.assertIn("## 用途", body or "")

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

    def test_event_row_visible(self):
        self.assertFalse(workspace.event_row_visible("file", "Documents/EVE"))
        self.assertTrue(workspace.event_row_visible("cursor", "forge"))
        self.assertTrue(workspace.event_row_visible("file", "dev/qr"))

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

        cfg = {"standards_auto_revise": True, "standards_auto_interval_hours": 168}
        with mock.patch.object(standards_auto.db, "session") as sess:
            conn = mock.MagicMock()
            sess.return_value.__enter__.return_value = conn
            standards_auto.db.get_state = mock.Mock(return_value=str(int(__import__("time").time()) - 100))
            self.assertFalse(standards_auto.should_run(cfg, force=False))
            self.assertTrue(standards_auto.should_run(cfg, force=True))


if __name__ == "__main__":
    unittest.main()
