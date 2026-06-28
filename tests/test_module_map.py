"""M9-1 模块地图。"""
from __future__ import annotations

import unittest

from qr import module_map


class TestModuleMap(unittest.TestCase):
    def test_areas_cover_main_modules(self):
        data = module_map.module_map()
        ids = {a["id"] for a in data["areas"]}
        self.assertTrue({"collectors", "index", "query", "web"}.issubset(ids))

    def test_files_exist(self):
        data = module_map.module_map()
        for area in data["areas"]:
            for f in area.get("files") or []:
                self.assertTrue(
                    __import__("pathlib").Path(f["path"]).is_file(),
                    msg=f["path"],
                )


if __name__ == "__main__":
    unittest.main()
