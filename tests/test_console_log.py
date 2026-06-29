"""console_log 单元测试。"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from qr import console_log


class TestConsoleLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_path = Path(self.tmp.name) / "console.jsonl"
        self.patches = [
            mock.patch.object(console_log, "_LOG_PATH", self.log_path),
            mock.patch.object(console_log.config, "LOGS_DIR", Path(self.tmp.name)),
            mock.patch.object(console_log.config, "ensure_dirs"),
        ]
        for p in self.patches:
            p.start()
        console_log._active_jobs.clear()
        console_log._line_count = 0

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_emit_and_tail(self):
        ev = console_log.emit(source="web", kind="stdout", text="hello")
        self.assertEqual(ev["source"], "web")
        self.assertTrue(self.log_path.is_file())
        rows = console_log.tail(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "hello")

    def test_job_lifecycle(self):
        jid = console_log.job_start(source="cli", label="索引")
        self.assertIn(jid, [j["job_id"] for j in console_log.active_jobs()])
        console_log.job_done(jid, source="cli", label="索引", text="完成")
        self.assertEqual(console_log.active_jobs(), [])

    def test_tail_filter_source(self):
        console_log.emit(source="web", kind="stdout", text="w")
        console_log.emit(source="cli", kind="stdout", text="c")
        web_only = console_log.tail(limit=10, source="web")
        self.assertEqual(len(web_only), 1)
        self.assertEqual(web_only[0]["text"], "w")

    def test_strip_ansi(self):
        raw = "\x1b[32mok\x1b[0m"
        self.assertEqual(console_log.strip_ansi(raw), "ok")

    def test_rotate_trims_old_lines(self):
        with mock.patch.object(console_log, "_MAX_LINES", 5):
            for i in range(8):
                console_log.emit(source="web", kind="stdout", text=f"line{i}")
            lines = self.log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertLessEqual(len(lines), 5)
            last = json.loads(lines[-1])
            self.assertEqual(last["text"], "line7")

    def test_concurrent_emit(self):
        def worker(n: int):
            for i in range(20):
                console_log.emit(source="web", kind="stdout", text=f"{n}-{i}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        rows = console_log.tail(limit=1000)
        self.assertEqual(len(rows), 80)


if __name__ == "__main__":
    unittest.main()
