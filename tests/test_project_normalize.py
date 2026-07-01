import sqlite3
import tempfile
import unittest
from pathlib import Path

from qr import db, project_normalize, workspace


class ProjectNormalizeTests(unittest.TestCase):
    def test_canonical_project_id(self):
        self.assertEqual(workspace.canonical_project_id("qr"), "dev/qr")
        self.assertEqual(workspace.canonical_project_id("cursor-qr"), "dev/qr")
        self.assertEqual(workspace.canonical_project_id("dev/qr"), "dev/qr")
        self.assertEqual(workspace.project_timeline_label("dev/qr"), "qr")
        self.assertEqual(workspace.project_timeline_label("dev/sports/project-sports"), "project-sports")

    def test_sanitize_display_legacy_qr(self):
        self.assertEqual(workspace.sanitize_display_project("qr"), "dev/qr")

    def test_migrate_legacy_events(self):
        from qr import config

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            old_home, old_db = config.QR_HOME, config.DB_PATH
            try:
                config.QR_HOME = td_path
                config.DB_PATH = td_path / "qr.db"
                db.init_db()
                with db.session() as conn:
                    conn.execute(
                        "INSERT INTO events(uid,ts,source,project,title,content) "
                        "VALUES('test-norm-1',1,'cursor','qr','q1','c1'),"
                        "('test-norm-2',2,'note','qr','n','')"
                    )
                    conn.commit()
                    stats = project_normalize.migrate_legacy_projects(conn, dry_run=False)
                    self.assertEqual(stats["events"], 2)
                    rows = conn.execute(
                        "SELECT project FROM events WHERE uid LIKE 'test-norm-%' ORDER BY uid"
                    ).fetchall()
                    self.assertEqual([r["project"] for r in rows], ["dev/qr", "dev/qr"])
            finally:
                config.QR_HOME = old_home
                config.DB_PATH = old_db

    def test_project_from_path_uses_registered_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "QR"
            proj = root / "dev" / "qr"
            nested = proj / "qr"
            nested.mkdir(parents=True)
            (proj / "README.md").write_text("# qr\n", encoding="utf-8")
            (proj / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (nested / "cli.py").write_text("print('hi')\n", encoding="utf-8")
            pid = workspace.project_from_path(nested / "cli.py", root)
            self.assertEqual(pid, "dev/qr")

    def test_index_project_for_path_nested_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "QR"
            proj = root / "dev" / "demo"
            proj.mkdir(parents=True)
            (proj / "README.md").write_text("# demo\n", encoding="utf-8")
            (proj / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            doc_path = proj / "src" / "main.py"
            doc_path.parent.mkdir(parents=True)
            doc_path.write_text("x=1\n", encoding="utf-8")
            cfg = {"workspace_root": str(root)}
            self.assertEqual(
                workspace.index_project_for_path(doc_path, "dev/demo/src", cfg),
                "dev/demo",
            )


if __name__ == "__main__":
    unittest.main()
