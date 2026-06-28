"""M8-3 迁移包导出/导入。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import bundle_export, config


class TestBundleExport(unittest.TestCase):
    def test_export_import_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "qrhome"
            home.mkdir()
            db_path = home / "qr.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                "CREATE TABLE events(id INTEGER);"
                "CREATE TABLE documents(id INTEGER);"
                "CREATE TABLE chunks(id INTEGER);"
                "CREATE TABLE state(key TEXT, value TEXT);"
            )
            conn.close()
            (home / "config.json").write_text("{}", encoding="utf-8")
            (home / "standards.md").write_text("# std\n", encoding="utf-8")
            zip_path = home / "out.zip"
            with mock.patch.object(config, "QR_HOME", home):
                exp = bundle_export.export_bundle(str(zip_path))
                self.assertTrue(Path(exp["path"]).is_file())
                imp = bundle_export.import_bundle(exp["path"], dest_home=str(home / "new"), dry_run=True)
            self.assertTrue(imp.get("ok"))
            self.assertIn("qr.db", imp.get("verified") or [])


if __name__ == "__main__":
    unittest.main()
