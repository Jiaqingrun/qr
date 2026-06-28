"""M4-2 仅出处问答模式。"""
from __future__ import annotations

import unittest
from unittest import mock

from qr import query


class TestCitationsOnly(unittest.TestCase):
    def test_format_citations_empty(self):
        text = query.format_citations([], question="测试")
        self.assertIn("未检索到", text)

    def test_format_citations_lists_hits(self):
        hits = [
            {
                "path": "/Users/qr/QR/dev/qr/README.md",
                "project": "dev/qr",
                "score": 0.87,
                "text": "QR 本地知识库说明",
            },
        ]
        text = query.format_citations(hits, question="知识库是什么")
        self.assertIn("README.md", text)
        self.assertIn("dev/qr", text)
        self.assertIn("0.87", text)
        self.assertIn("未生成回答", text)

    def test_citations_only_skips_chat_generate(self):
        sample = [
            {
                "path": "/a/x.md",
                "text": "body",
                "score": 0.5,
            },
        ]
        with mock.patch.object(query, "search", return_value=sample) as search_mock:
            with mock.patch("qr.query.Ollama") as Ollama:
                text, hits = query.citations_only("问题", k=3)
                search_mock.assert_called_once()
                Ollama.return_value.generate.assert_not_called()
        self.assertEqual(len(hits), 1)
        self.assertIn("x.md", text)


if __name__ == "__main__":
    unittest.main()
