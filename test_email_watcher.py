"""Tests for proactive email-to-Weixin forwarding."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest


PROJECT_DIR = Path(__file__).resolve().parent
ILINK_DIR = PROJECT_DIR / "ilink_catgirl"
if str(ILINK_DIR) not in sys.path:
    sys.path.insert(0, str(ILINK_DIR))

from email_watcher import EmailCursorStore, EmailWatcher, format_email_notification
from reminders import ReminderStore


class FakeLog:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def system(self, text: str) -> None:
        self.entries.append(("system", text))

    def assistant(self, text: str) -> None:
        self.entries.append(("assistant", text))

    def error(self, text: str) -> None:
        self.entries.append(("error", text))


class EmailWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.cursor_path = root / "email_cursor.json"
        self.store = ReminderStore(root / "reminders.sqlite3")
        self.user_id = "owner"
        self.store.record_inbound(self.user_id, "wechat-token")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _watcher(self, caller, sender, *, skip_existing=True) -> EmailWatcher:
        return EmailWatcher(
            self.store,
            caller,
            sender,
            FakeLog(),
            threading.Event(),
            threading.RLock(),
            owner_user_id=self.user_id,
            cursor_path=self.cursor_path,
            poll_interval=10,
            skip_existing=skip_existing,
        )

    def test_first_poll_skips_existing_then_new_mail_is_forwarded(self) -> None:
        calls: list[tuple[str, dict]] = []
        sent: list[tuple[str, str, str]] = []

        def caller(name: str, arguments: dict) -> str:
            calls.append((name, arguments))
            if name == "mailbox_status":
                return json.dumps({"highest_uid": 100})
            return json.dumps(
                {
                    "messages": [
                        {
                            "uid": 101,
                            "from": "sender@example.com",
                            "subject": "项目进展",
                            "date": "2026-08-28T10:00:00+08:00",
                            "body": "这是邮件正文摘要",
                            "attachments": ["report.pdf"],
                        }
                    ]
                },
                ensure_ascii=False,
            )

        watcher = self._watcher(
            caller,
            lambda user, token, text: sent.append((user, token, text)) or 1,
        )
        self.assertFalse(watcher.poll_once())
        self.assertEqual(EmailCursorStore(self.cursor_path).load(), 100)
        self.assertTrue(watcher.poll_once())

        self.assertEqual(calls[-1], ("check_new_mail", {"after_uid": 100, "limit": 5}))
        self.assertEqual(sent[0][0:2], (self.user_id, "wechat-token"))
        self.assertIn("项目进展", sent[0][2])
        self.assertIn("外部邮件摘要", sent[0][2])
        self.assertEqual(EmailCursorStore(self.cursor_path).load(), 101)
        self.assertEqual(self.store.recipient(self.user_id)["outbound_count"], 1)

    def test_quota_exhaustion_does_not_query_or_advance_cursor(self) -> None:
        EmailCursorStore(self.cursor_path).save(8)
        self.store.record_outbound(self.user_id, 10)
        watcher = self._watcher(
            lambda *_: self.fail("额度耗尽时不应查询或推进邮箱游标"),
            lambda *_: self.fail("额度耗尽时不应发送"),
        )

        self.assertFalse(watcher.poll_once())
        self.assertEqual(EmailCursorStore(self.cursor_path).load(), 8)

    def test_notification_is_bounded(self) -> None:
        messages = [
            {"uid": 1, "from": "sender", "subject": "s" * 300, "body": "b" * 1000}
        ]
        notification = format_email_notification(messages, 300)
        self.assertLessEqual(len(notification), 300)
        self.assertIn("外部邮件摘要", notification)


if __name__ == "__main__":
    unittest.main()
