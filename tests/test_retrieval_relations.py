"""项目关系检索扩展（阶段 C）。"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import config, db, project_relations, query, retrieval_relations, workspace


class TestRetrievalRelations(unittest.TestCase):
    def test_expand_projects_one_hop(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(config, "DB_PATH", Path(td) / "t.db"), mock.patch.object(
                config, "QR_HOME", Path(td)
            ):
                db.init_db()
                with db.session() as conn:
                    project_relations.ensure_schema(conn)
                    conn.execute(
                        "INSERT INTO project_links "
                        "(from_project, to_project, link_type, strength, reason, evidence, "
                        "source, pinned, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            "dev/qr",
                            "dev/sports/project-sports",
                            "supports",
                            80,
                            "test",
                            "[]",
                            "manual",
                            1,
                            1,
                            1,
                        ),
                    )
                    conn.commit()
                    related = retrieval_relations.expand_projects("dev/qr")
                self.assertEqual(related, ["dev/sports/project-sports"])

    def test_tag_related_hit_discount(self):
        h = retrieval_relations.tag_related_hit(
            {"score": 1.0, "scores": {"final": 1.0}},
            anchor="dev/qr",
            related="dev/sports/project-sports",
            discount=0.85,
        )
        self.assertTrue(h["relation_expanded"])
        self.assertAlmostEqual(h["score"], 0.85)

    def test_search_merges_related_with_mock_core(self):
        calls: list[str | None] = []

        def fake_core(q, k, project=None, category=None):
            calls.append(project)
            if project == "dev/qr":
                return [{"path": "/QR/dev/qr/README.md", "score": 0.9, "text": "qr"}]
            if project == "dev/sports/project-sports":
                return [{"path": "/QR/dev/sports/project-sports/README.md", "score": 0.8, "text": "sports"}]
            return []

        with mock.patch.object(query, "_search_core", side_effect=fake_core), mock.patch.object(
            retrieval_relations,
            "expand_projects",
            return_value=["dev/sports/project-sports"],
        ), mock.patch.object(
            config,
            "load_config",
            return_value={**config.DEFAULT_CONFIG, "retrieval_relation_expand": True},
        ):
            hits = query.search("schedule install", k=4, project="dev/qr")
        self.assertTrue(any(h.get("relation_expanded") for h in hits))
        self.assertIn("dev/qr", calls)
        self.assertIn("dev/sports/project-sports", calls)


if __name__ == "__main__":
    unittest.main()
