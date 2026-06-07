import unittest

from qr import activity_context, query


class TestActivityContext(unittest.TestCase):
    def test_is_activity_question(self):
        self.assertTrue(activity_context.is_activity_question("我最近做了点啥"))
        self.assertTrue(activity_context.is_activity_question("总结一下最近的工作"))
        self.assertFalse(activity_context.is_activity_question("qr index 怎么用"))

    def test_infer_window_days(self):
        self.assertEqual(activity_context.infer_window_days("今天做了什么"), 1)
        self.assertEqual(activity_context.infer_window_days("这周忙啥"), 7)
        self.assertEqual(activity_context.infer_window_days("最近做了点啥"), 7)

    def test_prepare_ask_uses_activity_not_early_exit(self):
        ctx = query.prepare_ask("我最近做了点啥")
        self.assertIsNone(ctx.get("early_answer"))
        self.assertIn("【近期行为摘要】", ctx.get("prompt") or "")
        self.assertIn("行为采集摘要", ctx.get("prompt") or "")


if __name__ == "__main__":
    unittest.main()
