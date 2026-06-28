"""M6-3 compliance 检出 PROJECT.md 混入全局规范。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qr import compliance, project_standards


class TestComplianceProjectMixed(unittest.TestCase):
    def test_detect_global_section_in_project_md(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "README.md").write_text("# x\n", encoding="utf-8")
            (d / "AGENTS.md").write_text("agents\n", encoding="utf-8")
            (d / "PROJECT.md").write_text(
                "# 项目约定\n\n## 一、存储与目录规范\n\n## 用途\n测试\n",
                encoding="utf-8",
            )
            with mock.patch("qr.compliance.config.STANDARDS_PATH", Path(td) / "s.md"):
                (Path(td) / "s.md").write_text("# std\n", encoding="utf-8")
                with mock.patch(
                    "qr.workspace.is_under_workspace", return_value=True
                ):
                    rep = compliance.check_project(d)
        self.assertFalse(rep["ok"])
        self.assertTrue(
            any("混入全局规范" in i for i in rep["issues"]),
            rep["issues"],
        )

    def test_conda_line_flagged(self):
        body = (
            "# 项目约定\n\n## 用途\nx\n\n"
            "conda create -n qr python=3.12\n"
        )
        issues = project_standards.mixed_standards_issues(body)
        self.assertTrue(any("conda" in i for i in issues))


if __name__ == "__main__":
    unittest.main()
