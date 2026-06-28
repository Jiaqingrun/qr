import unittest

from qr import usage


class TestUsageGameFilter(unittest.TestCase):
    def test_steam_excluded(self):
        self.assertTrue(usage.is_excluded_usage("Steam", "com.valvesoftware.steam"))

    def test_eve_excluded(self):
        self.assertTrue(usage.is_excluded_usage("EVE Online", "com.ccpgames.eve"))

    def test_dev_project_whitelisted(self):
        cfg = {"usage_exclude_games": True, "usage_include_apps": ["华夏重工"]}
        self.assertFalse(usage.is_excluded_usage("华夏重工", "com.huaxia.heavy", cfg))

    def test_cursor_not_excluded(self):
        self.assertFalse(usage.is_excluded_usage("Cursor", "com.todesktop.230313mzl4w4u92"))

    def test_games_off(self):
        cfg = {"usage_exclude_games": False}
        self.assertFalse(usage.is_excluded_usage("Steam", "com.valvesoftware.steam", cfg))


if __name__ == "__main__":
    unittest.main()
