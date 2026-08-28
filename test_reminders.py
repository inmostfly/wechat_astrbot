"""Tests for the SQLite reminder store, tools, and scheduler."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone


PROJECT_DIR = Path(__file__).resolve().parent
ILINK_DIR = PROJECT_DIR / "ilink_catgirl"
if str(ILINK_DIR) not in sys.path:
    sys.path.insert(0, str(ILINK_DIR))

from reminders import (
    ReminderScheduler,
    ReminderStore,
    ReminderTools,
    execute_reminder_action,
)


class FakeLog:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def system(self, text: str) -> None:
        self.entries.append(("system", text))

    def assistant(self, text: str) -> None:
        self.entries.append(("assistant", text))

    def error(self, text: str) -> None:
        self.entries.append(("error", text))


class ReminderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "reminders.sqlite3"
        self.store = ReminderStore(self.database)
        self.user_id = "owner-user"
        self.store.record_inbound(self.user_id, "fresh-token")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _status(self, reminder_id: int) -> tuple[str, float, str | None]:
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                "SELECT status, run_at, last_error FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
        assert row is not None
        return str(row[0]), float(row[1]), row[2]

    def _reminder_row(self, reminder_id: int) -> dict:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
            ).fetchone()
        assert row is not None
        return dict(row)

    def _create_due(self, *, repeat_kind: str = "once") -> int:
        return self.store.create_reminder(
            self.user_id,
            "测试提醒",
            time.time() - 1,
            timezone_name="Asia/Shanghai",
            repeat_kind=repeat_kind,
        )

    def test_sqlite_persists_and_inbound_resets_quota(self) -> None:
        self.store.record_outbound(self.user_id, 4)
        reopened = ReminderStore(self.database)
        self.assertEqual(reopened.recipient(self.user_id)["outbound_count"], 4)

        reopened.record_inbound(self.user_id, "new-token")
        recipient = reopened.recipient(self.user_id)
        self.assertEqual(recipient["context_token"], "new-token")
        self.assertEqual(recipient["outbound_count"], 0)

    def test_existing_database_is_migrated_for_action_columns(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE recipients (
                    user_id TEXT PRIMARY KEY,
                    context_token TEXT NOT NULL,
                    last_inbound_at REAL NOT NULL,
                    outbound_count INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    run_at REAL NOT NULL,
                    timezone TEXT NOT NULL,
                    repeat_kind TEXT NOT NULL DEFAULT 'once',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    sent_at REAL,
                    last_error TEXT
                );
                """
            )
            connection.commit()

        ReminderStore(legacy_path)
        with closing(sqlite3.connect(legacy_path)) as connection:
            reminder_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(reminders)")
            }
            recipient_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(recipients)")
            }
        self.assertIn("action_kind", reminder_columns)
        self.assertIn("action_args", reminder_columns)
        self.assertIn("schedule_args", reminder_columns)
        self.assertIn("last_checkin_at", recipient_columns)

        migrated = ReminderStore(legacy_path)
        migrated.record_inbound("legacy-user", "token")
        weekly_id = migrated.create_reminder(
            "legacy-user",
            "迁移后每周提醒",
            time.time() + 60,
            timezone_name="Asia/Shanghai",
            repeat_kind="weekly",
            schedule_args={"weekdays": [1]},
        )
        self.assertGreater(weekly_id, 0)

    def test_proactive_checkin_is_sent_once_and_added_to_context(self) -> None:
        self.store.record_inbound(
            self.user_id,
            "checkin-token",
            received_at=time.time() - 23.5 * 3600,
        )
        sent: list[tuple[str, str, str]] = []
        context: list[tuple[str, str]] = []
        candidates = ["今日任务刷新啦！", "请回复一句话补充联络能量！"]
        scheduler = ReminderScheduler(
            self.store,
            lambda user, token, text: sent.append((user, token, text)) or 1,
            FakeLog(),
            threading.Event(),
            threading.RLock(),
            checkin_enabled=True,
            checkin_after_hours=23,
            checkin_messages=candidates,
            context_recorder=lambda user, text: context.append((user, text)),
            owner_user_id=self.user_id,
        )

        scheduler._send_due_checkins()
        scheduler._send_due_checkins()

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0:2], (self.user_id, "checkin-token"))
        self.assertIn(sent[0][2], candidates)
        self.assertEqual(context, [(self.user_id, sent[0][2])])
        recipient = self.store.recipient(self.user_id)
        self.assertIsNotNone(recipient["last_checkin_at"])
        self.assertEqual(recipient["outbound_count"], 1)

    def test_proactive_checkin_waits_until_threshold(self) -> None:
        sent: list[str] = []
        scheduler = ReminderScheduler(
            self.store,
            lambda _user, _token, text: sent.append(text) or 1,
            FakeLog(),
            threading.Event(),
            threading.RLock(),
            checkin_enabled=True,
            checkin_after_hours=23,
            checkin_messages=["测试问候"],
        )

        scheduler._send_due_checkins()

        self.assertEqual(sent, [])

    def test_tools_create_list_and_cancel_without_writing_sql(self) -> None:
        tools = ReminderTools(
            self.store,
            timezone_name="Asia/Shanghai",
            owner_user_id=self.user_id,
        )
        created = json.loads(
            tools.call(
                self.user_id,
                "create_reminder",
                {"content": "十分钟后休息", "delay_minutes": 10},
            )
        )
        reminder_id = created["reminder_id"]
        listed = json.loads(tools.call(self.user_id, "list_reminders", {}))
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["reminders"][0]["id"], reminder_id)
        self.assertEqual(listed["reminders"][0]["type_label"], "日常提醒任务")
        self.assertIn("安洁莉娜", listed["presentation_hint"])

        cancelled = json.loads(
            tools.call(self.user_id, "cancel_reminder", {"reminder_id": reminder_id})
        )
        self.assertTrue(cancelled["ok"])
        self.assertEqual(
            json.loads(tools.call(self.user_id, "list_reminders", {}))["count"], 0
        )

    def test_weather_schedule_stores_executable_action(self) -> None:
        tools = ReminderTools(self.store, timezone_name="Asia/Shanghai")
        created = json.loads(
            tools.call(
                self.user_id,
                "create_weather_schedule",
                {
                    "location": "河南省邓州市",
                    "forecast_days": 3,
                    "delay_minutes": 10,
                    "repeat": "daily",
                },
            )
        )
        row = self._reminder_row(created["reminder_id"])
        self.assertEqual(row["action_kind"], "weather")
        self.assertEqual(json.loads(row["action_args"])["location"], "河南省邓州市")
        self.assertEqual(row["repeat_kind"], "daily")

    def test_weekly_reminder_accepts_multiple_weekdays_and_lists_them(self) -> None:
        tools = ReminderTools(self.store, timezone_name="Asia/Shanghai")
        created = json.loads(
            tools.call(
                self.user_id,
                "create_reminder",
                {
                    "content": "提交周报",
                    "run_at": "20:30",
                    "repeat": "weekly",
                    "weekdays": [5, 1, 5],
                },
            )
        )

        self.assertTrue(created["ok"])
        self.assertEqual(created["weekdays"], [1, 5])
        row = self._reminder_row(created["reminder_id"])
        self.assertEqual(row["repeat_kind"], "weekly")
        self.assertEqual(json.loads(row["schedule_args"]), {"weekdays": [1, 5]})
        listed = json.loads(tools.call(self.user_id, "list_reminders", {}))
        self.assertEqual(listed["reminders"][0]["schedule_text"], "每周一、周五 20:30")

    def test_weekly_reminder_requires_weekdays_and_hhmm(self) -> None:
        tools = ReminderTools(self.store, timezone_name="Asia/Shanghai")
        missing_days = json.loads(
            tools.call(
                self.user_id,
                "create_reminder",
                {"content": "测试", "run_at": "08:00", "repeat": "weekly"},
            )
        )
        bad_delay = json.loads(
            tools.call(
                self.user_id,
                "create_reminder",
                {
                    "content": "测试",
                    "delay_minutes": 10,
                    "repeat": "weekly",
                    "weekdays": [1],
                },
            )
        )

        self.assertIn("weekdays", missing_days["error"])
        self.assertIn("不能使用 delay_minutes", bad_delay["error"])

    def test_weather_is_queried_when_task_triggers(self) -> None:
        reminder_id = self.store.create_reminder(
            self.user_id,
            "查询河南省邓州市天气（未来1天）",
            time.time() - 1,
            timezone_name="Asia/Shanghai",
            repeat_kind="once",
            action_kind="weather",
            action_args={"location": "河南省邓州市", "forecast_days": 1},
        )
        weather_calls: list[dict] = []
        sent: list[str] = []

        def fake_weather(name: str, arguments: dict) -> str:
            self.assertEqual(name, "get_weather")
            weather_calls.append(arguments)
            return json.dumps(
                {
                    "query": arguments["location"],
                    "resolved_location": "邓州，南阳，河南省，中国",
                    "current": {
                        "condition": "晴",
                        "temperature_c": 29,
                        "apparent_temperature_c": 31,
                        "relative_humidity_percent": 60,
                        "wind_direction": "东风",
                        "wind_scale": "2",
                    },
                    "forecast": [
                        {
                            "date": "2026-08-17",
                            "condition": "晴转多云",
                            "temperature_min_c": 23,
                            "temperature_max_c": 32,
                            "precipitation_sum_mm": 0,
                        }
                    ],
                    "provider_update_time": "2026-08-16T20:00+08:00",
                    "source_url": "https://www.qweather.com/",
                },
                ensure_ascii=False,
            )

        scheduler = ReminderScheduler(
            self.store,
            lambda _user, _token, text: sent.append(text) or 1,
            FakeLog(),
            threading.Event(),
            threading.RLock(),
            action_executor=lambda reminder: execute_reminder_action(
                reminder, fake_weather
            ),
        )
        scheduler._deliver_group(self.user_id, self.store.claim_due(time.time()))

        self.assertEqual(len(weather_calls), 1)
        self.assertEqual(weather_calls[0]["location"], "河南省邓州市")
        self.assertIn("邓州，南阳", sent[0])
        self.assertIn("当前：晴", sent[0])
        self.assertIn("2026-08-17：晴转多云", sent[0])
        self.assertEqual(self._status(reminder_id)[0], "sent")

    def test_due_reminder_is_sent_and_counted(self) -> None:
        reminder_id = self._create_due()
        sent: list[tuple[str, str, str]] = []

        def sender(user_id: str, context_token: str, text: str) -> int:
            sent.append((user_id, context_token, text))
            return 1

        scheduler = ReminderScheduler(
            self.store,
            sender,
            FakeLog(),
            threading.Event(),
            threading.RLock(),
        )
        due = self.store.claim_due(time.time())
        scheduler._deliver_group(self.user_id, due)

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1], "fresh-token")
        self.assertIn("测试提醒", sent[0][2])
        self.assertEqual(self._status(reminder_id)[0], "sent")
        self.assertEqual(self.store.recipient(self.user_id)["outbound_count"], 1)

    def test_single_reminder_uses_lively_intro_and_records_context(self) -> None:
        reminder_id = self._create_due()
        sent: list[str] = []
        context: list[tuple[str, str]] = []
        scheduler = ReminderScheduler(
            self.store,
            lambda _user, _token, text: sent.append(text) or 1,
            FakeLog(),
            threading.Event(),
            threading.RLock(),
            reminder_intros=["Dr.风云飘飘，这份提醒已经送达。"],
            context_recorder=lambda user, text: context.append((user, text)),
        )

        scheduler._deliver_group(self.user_id, self.store.claim_due(time.time()))

        self.assertEqual(
            sent,
            ["Dr.风云飘飘，这份提醒已经送达。\n测试提醒"],
        )
        self.assertNotIn("1. 测试提醒", sent[0])
        self.assertEqual(context, [(self.user_id, sent[0])])
        self.assertEqual(self._status(reminder_id)[0], "sent")

    def test_quota_and_24_hour_window_wait_for_reactivation(self) -> None:
        quota_id = self._create_due()
        self.store.record_outbound(self.user_id, 10)
        scheduler = ReminderScheduler(
            self.store,
            lambda *_: self.fail("额度耗尽时不应发送"),
            FakeLog(),
            threading.Event(),
            threading.RLock(),
        )
        scheduler._deliver_group(self.user_id, self.store.claim_due(time.time()))
        self.assertEqual(self._status(quota_id)[0], "waiting_reactivation")

        self.store.record_inbound(
            self.user_id, "expired-token", received_at=time.time() - 25 * 3600
        )
        expired_id = self._create_due()
        scheduler._deliver_group(self.user_id, self.store.claim_due(time.time()))
        self.assertEqual(self._status(expired_id)[0], "waiting_reactivation")

        self.store.record_inbound(self.user_id, "refreshed-token")
        self.assertEqual(self.store.reactivate_waiting(self.user_id), 2)
        self.assertEqual(self._status(quota_id)[0], "pending")

    def test_daily_reminder_moves_to_next_day(self) -> None:
        reminder_id = self._create_due(repeat_kind="daily")
        due = self.store.claim_due(time.time())
        previous_run = float(due[0]["run_at"])
        self.store.mark_delivered(due, time.time())
        status, next_run, _ = self._status(reminder_id)
        self.assertEqual(status, "pending")
        self.assertGreater(next_run, time.time())
        self.assertGreaterEqual(next_run - previous_run, 23 * 3600)

    def test_weekly_reminder_moves_to_next_selected_weekday(self) -> None:
        china = timezone(timedelta(hours=8))
        previous = datetime(2026, 8, 28, 20, 30, tzinfo=china)  # Friday
        delivered = datetime(2026, 8, 28, 20, 31, tzinfo=china)
        reminder_id = self.store.create_reminder(
            self.user_id,
            "每周任务",
            previous.timestamp(),
            timezone_name="Asia/Shanghai",
            repeat_kind="weekly",
            schedule_args={"weekdays": [1, 5]},
        )
        due = self.store.claim_due(delivered.timestamp())

        self.store.mark_delivered(due, delivered.timestamp())

        status, next_run, _ = self._status(reminder_id)
        next_local = datetime.fromtimestamp(next_run, china)
        self.assertEqual(status, "pending")
        self.assertEqual(next_local, datetime(2026, 8, 31, 20, 30, tzinfo=china))

    def test_ret_minus_two_waits_for_new_inbound(self) -> None:
        reminder_id = self._create_due()

        def rejected_sender(*_: str) -> int:
            raise RuntimeError("sendmessage 失败：ret=-2，无说明")

        scheduler = ReminderScheduler(
            self.store,
            rejected_sender,
            FakeLog(),
            threading.Event(),
            threading.RLock(),
        )
        scheduler._deliver_group(self.user_id, self.store.claim_due(time.time()))
        status, _, error = self._status(reminder_id)
        self.assertEqual(status, "waiting_reactivation")
        self.assertIn("等待用户", error)


if __name__ == "__main__":
    unittest.main()
