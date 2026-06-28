"""M4-1 扩展评测题集（core / extended 分栏）。"""
from __future__ import annotations

import unittest

from qr import eval_suite


class TestEvalExtended(unittest.TestCase):
    def test_core_case_count(self):
        cases = eval_suite.load_cases(include_extended=False)
        self.assertEqual(len(cases), 9)
        self.assertTrue(all(eval_suite.case_tier_group(c) == "core" for c in cases))

    def test_extended_case_count(self):
        all_cases = eval_suite.load_cases(include_extended=True)
        ext = [c for c in all_cases if eval_suite.case_tier_group(c) == "extended"]
        self.assertGreaterEqual(len(ext), 6)

    def test_summarize_rag_split(self):
        rows = [
            {"tier": "core", "retrieval_ok": True, "retrieval_forbidden": False, "search_s": 0.1},
            {"tier": "core", "retrieval_ok": False, "retrieval_forbidden": False, "search_s": 0.2},
            {"tier": "extended", "retrieval_ok": True, "retrieval_forbidden": False, "search_s": 0.3},
        ]
        split = eval_suite.summarize_rag_split(rows)
        self.assertEqual(split["core"]["cases"], 2)
        self.assertEqual(split["core"]["retrieval_ok"], 1)
        self.assertEqual(split["extended"]["cases"], 1)
        self.assertEqual(split["extended"]["retrieval_ok"], 1)

    def test_extended_reference_has_doc(self):
        refs = eval_suite.extended_cases_reference()
        self.assertGreaterEqual(len(refs), 6)
        for c in refs:
            self.assertEqual(c.get("tier"), "extended")
            self.assertTrue(c.get("expect_paths"))
            self.assertTrue(c.get("doc"))


if __name__ == "__main__":
    unittest.main()
