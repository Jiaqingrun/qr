"""评测结果聚合：model_pass_counts / eval_case_total。"""
from __future__ import annotations

import unittest

from qr import eval_suite


class TestEvalSuite(unittest.TestCase):
    def test_model_pass_counts_all_keys(self):
        data = {
            "results": {
                "qwen": [{"must_pass": True}, {"must_pass": False}],
                "deepseek": [{"must_pass": True}],
                "custom": [{"must_pass": True}, {"must_pass": True}, {"must_pass": False}],
            }
        }
        scores = eval_suite.model_pass_counts(data)
        self.assertEqual(scores, {"qwen": 1, "deepseek": 1, "custom": 2})
        self.assertEqual(eval_suite.eval_case_total(data), 3)


if __name__ == "__main__":
    unittest.main()
