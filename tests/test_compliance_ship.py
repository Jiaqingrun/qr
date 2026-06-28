"""M6-2 合规 --ship 验收清单。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import compliance, config, db


class TestComplianceShip(unittest.TestCase):
    def test_flags_missing_decision_and_ship(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / ".qr"
            home.mkdir()
            db_path = home / "qr.db"
            root = Path(td) / "QR"
            proj = root / "dev" / "qr"
            proj.mkdir(parents=True)
            (proj / "README.md").write_text("# test\n", encoding="utf-8")
            cfg = {
                "workspace_root": str(root),
                "project_categories": ["dev"],
                "compliance_ship_days": 14,
            }
            now = db.now()
            since = now - 3 * 86400
            with mock.patch.object(config, "QR_HOME", home), mock.patch.object(
                config, "DB_PATH", db_path
            ), mock.patch("qr.compliance.config.load_config", return_value=cfg):
                db.init_db()
                with db.session() as conn:
                    conn.execute(
                        "INSERT INTO events(uid,ts,source,project,title,content,meta) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (
                            "cursor:x:q0",
                            since + 100,
                            "cursor",
                            "dev/qr",
                            "q",
                            "body",
                            "{}",
                        ),
                    )
                    conn.commit()
                    rep = compliance.scan_ship_checks(conn, days=14, cfg=cfg)
            active = [p for p in rep["projects"] if p["project"] == "dev/qr"]
            self.assertEqual(len(active), 1)
            self.assertTrue(active[0]["active"])
            self.assertFalse(active[0]["ok"])
            self.assertIn("dev/qr", rep["missing_decisions"])
            self.assertIn("dev/qr", rep["missing_ship"])

    def test_ok_with_decision_and_ship_state(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / ".qr"
            home.mkdir()
            db_path = home / "qr.db"
            root = Path(td) / "QR"
            proj = root / "dev" / "qr"
            proj.mkdir(parents=True)
            (proj / "README.md").write_text("# test\n", encoding="utf-8")
            cfg = {
                "workspace_root": str(root),
                "project_categories": ["dev"],
                "compliance_ship_days": 14,
            }
            now = db.now()
            with mock.patch.object(config, "QR_HOME", home), mock.patch.object(
                config, "DB_PATH", db_path
            ), mock.patch("qr.compliance.config.load_config", return_value=cfg):
                db.init_db()
                with db.session() as conn:
                    conn.execute(
                        "INSERT INTO events(uid,ts,source,project,title,content,meta) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (
                            "cursor:y:q0",
                            now - 100,
                            "cursor",
                            "dev/qr",
                            "q",
                            "body",
                            "{}",
                        ),
                    )
                    conn.execute(
                        "INSERT INTO events(uid,ts,source,project,title,content,meta) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (
                            "note:dec1",
                            now - 50,
                            "note",
                            "dev/qr",
                            "决策",
                            "# 决策记录\n\n## 结论\nok",
                            "{}",
                        ),
                    )
                    db.set_state(conn, "ship_check_at:dev/qr", str(now - 10))
                    conn.commit()
                    rep = compliance.scan_ship_checks(conn, days=14, cfg=cfg)
            row = next(p for p in rep["projects"] if p["project"] == "dev/qr")
            self.assertTrue(row["ok"])
            self.assertEqual(row["decisions"], 1)
            self.assertTrue(row["ship_check"])


if __name__ == "__main__":
    unittest.main()
