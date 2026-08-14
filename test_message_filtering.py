"""Offline regression tests for WeChat message de-duplication."""

from __future__ import annotations

from collections import deque
import unittest

from my_catgirl import MessageTracker
from wechat_uia import Message, WeChat


class MessageFilteringTests(unittest.TestCase):
    def test_wechat_time_rows_are_system_content(self) -> None:
        system_rows = [
            "21:34",
            "昨天 21:34",
            "星期五 08:05",
            "8月14日 21:40",
            "2026年8月14日",
        ]
        for content in system_rows:
            self.assertTrue(WeChat._is_system_message_content(content))
        self.assertFalse(WeChat._is_system_message_content("明日天气"))

    def test_recent_outgoing_text_is_recognized(self) -> None:
        wx = WeChat.__new__(WeChat)
        wx._recent_outgoing = deque()
        wx._remember_outgoing("机器人回复")
        self.assertTrue(wx._is_recent_outgoing("机器人回复"))
        self.assertFalse(wx._is_recent_outgoing("用户消息"))

    def test_tracker_seeds_all_existing_rows(self) -> None:
        tracker = MessageTracker()
        old = [
            Message((1,), "friend", "退出"),
            Message((2,), "self", "下次见！"),
            Message((3,), "system", "21:34"),
        ]
        tracker.seed(old)
        self.assertEqual(tracker.new_friend_messages(old), [])

        new_friend = Message((4,), "friend", "明日天气")
        self.assertEqual(tracker.new_friend_messages([*old, new_friend]), [new_friend])
        self.assertEqual(tracker.new_friend_messages([new_friend]), [])

    def test_reused_runtime_id_with_new_content_is_new(self) -> None:
        tracker = MessageTracker()
        tracker.seed([Message((8,), "friend", "第一条")])
        replacement = Message((8,), "friend", "第二条")
        self.assertEqual(tracker.new_friend_messages([replacement]), [replacement])


if __name__ == "__main__":
    unittest.main()
