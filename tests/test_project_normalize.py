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


if __name__ == "__main__":
    unittest.main()
