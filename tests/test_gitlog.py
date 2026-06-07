"""Git 采集：工作区 project_id 与 git_roots 边界。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qr import config, db, workspace
from qr.collectors import gitlog


class TestGitlog(unittest.TestCase):
    def test_git_roots_uses_git_scan_roots_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            qr = Path(tmp) / "QR"
            other = Path(tmp) / "other"
            qr.mkdir()
            other.mkdir()
            cfg = {
                "git_scan_roots": [str(qr)],
                "index_roots": [str(other)],
                "scatter_roots": [str(other)],
            }
            roots = config.git_roots(cfg)
            self.assertEqual(roots, [qr.resolve()])

    def test_project_from_path_for_nested_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "QR"
            repo = root / "dev" / "myapp"
            repo.mkdir(parents=True)
            pid = workspace.project_from_path(repo, root)
            self.assertEqual(pid, "dev/myapp")

    def test_collect_repo_writes_repo_meta_and_project_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "QR"
            repo = root / "dev" / "demo"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            db.init_db()
            block = (
                f"{gitlog._REC}deadbeef{gitlog._SEP}1700000000{gitlog._SEP}"
                f"Alice{gitlog._SEP}init repo{gitlog._SEP}\n"
            )
            fake = type("R", (), {"returncode": 0, "stdout": block})()
            with db.session() as conn:
                with patch.object(gitlog.workspace, "workspace_root", return_value=root), patch.object(
                    gitlog.subprocess, "run", return_value=fake
                ):
                    n = gitlog._collect_repo(conn, repo, backfill=True)
                self.assertEqual(n, 1)
                key = gitlog._repo_key(repo)
                uid = f"git:{key}:deadbeef"
                row = conn.execute(
                    "SELECT project, meta FROM events WHERE uid=?", (uid,)
                ).fetchone()
                conn.execute("DELETE FROM events WHERE uid=?", (uid,))
            self.assertIsNotNone(row)
            self.assertEqual(row["project"], "dev/demo")
            meta = json.loads(row["meta"])
            self.assertEqual(meta["author"], "Alice")
            self.assertEqual(meta["repo"], str(repo.resolve()))


if __name__ == "__main__":
    unittest.main()
