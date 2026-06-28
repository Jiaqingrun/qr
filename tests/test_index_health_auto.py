"""M4-3 索引健康自动清理。"""
from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from qr import config, db, index_health


class TestIndexHealthAuto(unittest.TestCase):
    @contextmanager
    def _temp_db(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / ".qr"
            home.mkdir()
            db_path = home / "qr.db"
            with mock.patch.object(config, "QR_HOME", home), mock.patch.object(
                config, "DB_PATH", db_path
            ):
                db.init_db()
                with db.session() as conn:
                    yield conn

    def test_maybe_auto_cleanup_skips_when_recent(self):
        with self._temp_db() as conn:
            db.set_state(conn, "index_health_auto_last", str(db.now()))
            with mock.patch("qr.index_health.scan", return_value={"missing_files": 5}):
                rep = index_health.maybe_auto_cleanup(conn)
        self.assertIsNone(rep)

    def test_force_cleanup_orphans(self):
        with self._temp_db() as conn:
            with mock.patch(
                "qr.index_health.scan",
                return_value={"missing_files": 2, "missing_samples": [{"path": "/x"}]},
            ):
                with mock.patch(
                    "qr.index_health.cleanup_orphans",
                    return_value={"documents_removed": 2, "chunks_removed": 4},
                ) as clean:
                    rep = index_health.maybe_auto_cleanup(conn, force=True)
        self.assertTrue(rep and rep.get("ran"))
        clean.assert_called_once()


if __name__ == "__main__":
    unittest.main()
