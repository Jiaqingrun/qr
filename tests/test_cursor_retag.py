import unittest

from qr import cursor_retag


class TestCursorRetag(unittest.TestCase):
    def test_known_sessions(self):
        self.assertEqual(len(cursor_retag.KNOWN_SPORTS_SESSIONS), 3)

    def test_sports_turn(self):
        self.assertTrue(
            cursor_retag.is_sports_turn(
                "我想要开发一套中考体育考试系统，你参考项目文件内容，有没有什么想法？",
            ),
        )
        self.assertFalse(
            cursor_retag.is_sports_turn(
                "下一关：在 Cursor 里真的用 ~/QR/dev/project-sports 开 workspace",
            ),
        )
        self.assertFalse(
            cursor_retag.is_sports_turn("全量自检"),
        )


if __name__ == "__main__":
    unittest.main()
