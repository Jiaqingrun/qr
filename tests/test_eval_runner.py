import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import config, eval_runner


class TestEvalRunner(unittest.TestCase):
    def test_run_model_eval_writes_logs(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td) / "logs"
            logs.mkdir()
            repo = Path(td) / "repo"
            scripts = repo / "scripts"
            scripts.mkdir(parents=True)
            script = scripts / "model_eval.py"
            script.write_text(
                "from qr import config\n"
                "import json\n"
                "p = config.LOGS_DIR / 'model_eval.json'\n"
                "config.ensure_dirs()\n"
                "p.write_text(json.dumps({'ok': True}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            def fake_run(cmd, **kwargs):
                (logs / "model_eval.json").write_text(
                    json.dumps({"ok": True}), encoding="utf-8"
                )
                return mock.Mock(returncode=0, stdout="WROTE", stderr="")

            with mock.patch.object(config, "LOGS_DIR", logs), mock.patch.object(
                config, "REPO_ROOT", repo
            ), mock.patch.object(eval_runner.subprocess, "run", side_effect=fake_run):
                result = eval_runner.run_model_eval(timeout=30)
            self.assertTrue(result["ok"])
            self.assertTrue((logs / "model_eval.json").is_file())
            snaps = list(logs.glob("model_eval-*.json"))
            self.assertEqual(len(snaps), 1)
            self.assertEqual(json.loads(snaps[0].read_text(encoding="utf-8")), {"ok": True})

    def test_render_eval_markdown_and_log_path(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td) / "logs"
            logs.mkdir()
            data = {
                "chat_model": "qwen2.5:32b",
                "deep_model": "deepseek-r1:32b",
                "rag_summary": {
                    "cases": 2,
                    "retrieval_ok": 2,
                    "retrieval_rate": 100.0,
                    "forbidden_hits": 0,
                    "search_avg": 0.5,
                },
                "rag_baseline": [],
                "results": {
                    "qwen": [
                        {
                            "case": "port",
                            "tier": "core",
                            "model": "qwen2.5:32b",
                            "must_pass": True,
                            "retrieval_ok": True,
                            "ask_s": 1.2,
                        }
                    ],
                },
            }
            with mock.patch.object(config, "LOGS_DIR", logs):
                md = eval_runner.render_eval_markdown(data, generated_ts=1717200000)
                path = eval_runner.write_eval_log_markdown(data, generated_ts=1717200000)
            self.assertIn("2024-06", md)
            self.assertIn("qwen2.5:32b", md)
            self.assertEqual(path.name, "eval-202406.md")
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
