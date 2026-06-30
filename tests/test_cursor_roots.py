"""M3-2 Cursor 工作区注册表（cursor_roots）。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import config, workspace


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE events (uid TEXT PRIMARY KEY, ts INTEGER, source TEXT, "
        "project TEXT, title TEXT, content TEXT, meta TEXT);"
        "CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT);"
    )
    return conn


class TestCursorRoots(unittest.TestCase):
    def test_resolve_dev_qr_slug(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "QR"
            proj = root / "dev" / "qr"
            proj.mkdir(parents=True)
            (proj / "README.md").write_text("# qr\n", encoding="utf-8")
            cfg = {
                "workspace_root": str(root),
                "project_categories": ["dev"],
            }
            slug = workspace._cursor_dir_slug(proj)
            pid, review = workspace.resolve_cursor_project(slug, cfg)
            self.assertEqual(pid, "dev/qr")
            self.assertFalse(review)

    def test_unknown_slug_needs_review(self):
        pid, review = workspace.resolve_cursor_project("totally-unknown-slug-xyz", {})
        self.assertIsNone(pid)
        self.assertTrue(review)

    def test_no_tail_fallback_for_qr_root_slug(self):
        """打开 ~/QR 根目录时不应误映射为 dev/qr。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "QR"
            root.mkdir(parents=True)
            cfg = {"workspace_root": str(root), "project_categories": ["dev"]}
            root_slug = workspace._cursor_dir_slug(root)
            mapped = workspace.project_from_cursor_dir_name(root_slug, cfg)
            self.assertEqual(mapped, "")
            pid, review = workspace.resolve_cursor_project(root_slug, cfg)
            self.assertIsNone(pid)
            self.assertTrue(review)

    def test_recommended_cursor_open_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "QR"
            proj = root / "dev" / "qr"
            proj.mkdir(parents=True)
            cfg = {"workspace_root": str(root), "project_categories": ["dev"]}
            path = workspace.recommended_cursor_open_path("dev/qr", cfg)
            self.assertEqual(path, str(proj.resolve()))

    def test_remap_legacy_qr_events(self):
        conn = _mem_conn()
        conn.execute(
            "INSERT INTO events(uid,ts,source,project,title) VALUES(?,?,?,?,?)",
            ("cursor:u1:q0", 1, "cursor", "qr", "q"),
        )
        conn.commit()
        with mock.patch.object(workspace, "sync_cursor_roots_registry", return_value={}):
            stats = workspace.remap_cursor_event_projects(conn, dry_run=False)
        row = conn.execute("SELECT project FROM events WHERE uid='cursor:u1:q0'").fetchone()
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(row["project"], "dev/qr")

    def test_cursor_collector_resolve_meta(self):
        from qr.collectors import cursor as cur

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "QR"
            proj = root / "dev" / "syspeek"
            proj.mkdir(parents=True)
            cfg = {"workspace_root": str(root), "project_categories": ["dev"]}
            slug = workspace._cursor_dir_slug(proj)
            with mock.patch.object(workspace, "resolve_cursor_project", return_value=(None, True)):
                project, meta = cur._resolve_project(slug, cfg)
            self.assertEqual(project, "")
            self.assertTrue(meta.get("needs_review"))
            self.assertEqual(meta.get("cursor_slug"), slug)


if __name__ == "__main__":
    unittest.main()
