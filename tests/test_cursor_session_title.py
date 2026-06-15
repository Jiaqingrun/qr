import unittest

from qr import cursor_session_title as cst


class TestCursorSessionTitle(unittest.TestCase):
    def test_should_include_execute_only(self):
        self.assertTrue(cst.should_include_in_prompt_guides("执行-QR 知识库 · 工作区分离"))
        self.assertFalse(cst.should_include_in_prompt_guides("参考-QR本地知识库"))
        self.assertFalse(cst.should_include_in_prompt_guides("参考-AI使用水平评估"))
        self.assertFalse(cst.should_include_in_prompt_guides("Stable Diffusion XL machine capabilities"))
        self.assertFalse(cst.should_include_in_prompt_guides("草稿-未登记前缀"))

    def test_session_title_policy(self):
        self.assertEqual(cst.session_title_policy("执行-太一"), "execute")
        self.assertEqual(cst.session_title_policy("参考-远程连接树莓派"), "reference")
        self.assertEqual(cst.session_title_policy("无连字符标题"), "pending")
        self.assertEqual(cst.session_title_policy("未来-新前缀"), "unknown_prefix")

    def test_parse_session_prefix(self):
        self.assertEqual(cst.parse_session_prefix("执行-Pdf 页面统计工具"), "执行")
        self.assertIsNone(cst.parse_session_prefix("还没改标题"))


if __name__ == "__main__":
    unittest.main()
