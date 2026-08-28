"""SQLite-backed reminder tools and scheduler for the iLink bot."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, tzinfo
import json
from pathlib import Path
import random
import re
import sqlite3
import threading
import time
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


UTC = timezone.utc
ACTIVE_STATUSES = ("pending", "waiting_reactivation", "sending", "failed")
WEEKDAY_LABELS = {
    1: "周一",
    2: "周二",
    3: "周三",
    4: "周四",
    5: "周五",
    6: "周六",
    7: "周日",
}


def _now_timestamp() -> float:
    return time.time()


def _timezone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        # Minimal Windows Python installations may not ship IANA timezone data.
        # China has used UTC+8 without daylight-saving changes since 1991.
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name)
        raise ValueError(f"未知时区：{name}") from error


def _next_daily_timestamp(previous: float, now: float, timezone_name: str) -> float:
    zone = _timezone(timezone_name)
    previous_local = datetime.fromtimestamp(previous, UTC).astimezone(zone)
    candidate = previous_local + timedelta(days=1)
    while candidate.timestamp() <= now:
        candidate += timedelta(days=1)
    return candidate.timestamp()


def _normalize_weekdays(values: Any) -> list[int]:
    if not isinstance(values, list) or not values:
        raise ValueError("每周提醒必须提供 weekdays，例如 [1, 3, 5]；1代表周一，7代表周日")
    try:
        weekdays = sorted({int(value) for value in values})
    except (TypeError, ValueError) as error:
        raise ValueError("weekdays 必须是1到7组成的数组") from error
    if any(value < 1 or value > 7 for value in weekdays):
        raise ValueError("weekdays 只允许1到7；1代表周一，7代表周日")
    return weekdays


def _next_weekly_datetime(
    now: datetime,
    weekdays: list[int],
    hour: int,
    minute: int,
) -> datetime:
    for offset in range(8):
        candidate = (now + timedelta(days=offset)).replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0,
        )
        if candidate.isoweekday() in weekdays and candidate > now:
            return candidate
    raise ValueError("无法计算下一次每周提醒时间")


def _next_weekly_timestamp(
    previous: float,
    now: float,
    timezone_name: str,
    schedule_args: str,
) -> float:
    zone = _timezone(timezone_name)
    previous_local = datetime.fromtimestamp(previous, UTC).astimezone(zone)
    now_local = datetime.fromtimestamp(now, UTC).astimezone(zone)
    try:
        arguments = json.loads(schedule_args or "{}")
    except json.JSONDecodeError as error:
        raise ValueError("每周提醒的调度参数损坏") from error
    weekdays = _normalize_weekdays(arguments.get("weekdays"))
    return _next_weekly_datetime(
        now_local,
        weekdays,
        previous_local.hour,
        previous_local.minute,
    ).timestamp()


class ReminderStore:
    """Persist recipients and reminders in a single local SQLite database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS recipients (
                    user_id TEXT PRIMARY KEY,
                    context_token TEXT NOT NULL,
                    last_inbound_at REAL NOT NULL,
                    last_checkin_at REAL,
                    outbound_count INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    run_at REAL NOT NULL,
                    timezone TEXT NOT NULL,
                    repeat_kind TEXT NOT NULL DEFAULT 'once'
                        CHECK (repeat_kind IN ('once', 'daily', 'weekly')),
                    schedule_args TEXT NOT NULL DEFAULT '{}',
                    action_kind TEXT NOT NULL DEFAULT 'message'
                        CHECK (action_kind IN ('message', 'weather')),
                    action_args TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN (
                            'pending', 'sending', 'waiting_reactivation',
                            'sent', 'cancelled', 'failed'
                        )),
                    created_at REAL NOT NULL,
                    sent_at REAL,
                    last_error TEXT,
                    FOREIGN KEY (user_id) REFERENCES recipients(user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(status, run_at);
                CREATE INDEX IF NOT EXISTS idx_reminders_user
                    ON reminders(user_id, status, run_at);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(reminders)")
            }
            if "action_kind" not in columns:
                connection.execute(
                    "ALTER TABLE reminders ADD COLUMN action_kind TEXT NOT NULL DEFAULT 'message'"
                )
            if "action_args" not in columns:
                connection.execute(
                    "ALTER TABLE reminders ADD COLUMN action_args TEXT NOT NULL DEFAULT '{}'"
                )
            if "schedule_args" not in columns:
                connection.execute(
                    "ALTER TABLE reminders ADD COLUMN schedule_args TEXT NOT NULL DEFAULT '{}'"
                )
            table_sql_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'reminders'"
            ).fetchone()
            table_sql = str(table_sql_row["sql"] or "") if table_sql_row else ""
            if "'weekly'" not in table_sql:
                self._upgrade_reminders_for_weekly(connection)
            recipient_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(recipients)")
            }
            if "last_checkin_at" not in recipient_columns:
                connection.execute(
                    "ALTER TABLE recipients ADD COLUMN last_checkin_at REAL"
                )
            # A process may have stopped after claiming a task but before completing it.
            connection.execute(
                """
                UPDATE reminders
                SET status = 'pending', last_error = '程序上次在发送过程中停止，已重新排队'
                WHERE status = 'sending'
                """
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _upgrade_reminders_for_weekly(connection: sqlite3.Connection) -> None:
        """Rebuild the table because SQLite cannot alter an existing CHECK constraint."""

        connection.executescript(
            """
            DROP INDEX IF EXISTS idx_reminders_due;
            DROP INDEX IF EXISTS idx_reminders_user;
            ALTER TABLE reminders RENAME TO reminders_before_weekly;

            CREATE TABLE reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                content TEXT NOT NULL,
                run_at REAL NOT NULL,
                timezone TEXT NOT NULL,
                repeat_kind TEXT NOT NULL DEFAULT 'once'
                    CHECK (repeat_kind IN ('once', 'daily', 'weekly')),
                schedule_args TEXT NOT NULL DEFAULT '{}',
                action_kind TEXT NOT NULL DEFAULT 'message'
                    CHECK (action_kind IN ('message', 'weather')),
                action_args TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'sending', 'waiting_reactivation',
                        'sent', 'cancelled', 'failed'
                    )),
                created_at REAL NOT NULL,
                sent_at REAL,
                last_error TEXT,
                FOREIGN KEY (user_id) REFERENCES recipients(user_id)
            );

            INSERT INTO reminders (
                id, user_id, content, run_at, timezone, repeat_kind,
                schedule_args, action_kind, action_args, status,
                created_at, sent_at, last_error
            )
            SELECT
                id, user_id, content, run_at, timezone, repeat_kind,
                schedule_args, action_kind, action_args, status,
                created_at, sent_at, last_error
            FROM reminders_before_weekly;

            DROP TABLE reminders_before_weekly;
            CREATE INDEX idx_reminders_due ON reminders(status, run_at);
            CREATE INDEX idx_reminders_user ON reminders(user_id, status, run_at);
            """
        )

    def record_inbound(
        self,
        user_id: str,
        context_token: str,
        *,
        received_at: float | None = None,
    ) -> None:
        received_at = received_at or _now_timestamp()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO recipients (
                    user_id, context_token, last_inbound_at, outbound_count, updated_at
                ) VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    context_token = excluded.context_token,
                    last_inbound_at = excluded.last_inbound_at,
                    outbound_count = 0,
                    updated_at = excluded.updated_at
                """,
                (user_id, context_token, received_at, received_at),
            )

    def record_outbound(self, user_id: str, count: int = 1) -> None:
        count = max(0, int(count))
        if count == 0:
            return
        now = _now_timestamp()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE recipients
                SET outbound_count = outbound_count + ?, updated_at = ?
                WHERE user_id = ?
                """,
                (count, now, user_id),
            )

    def recipient(self, user_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM recipients WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def due_checkin_recipients(
        self,
        now: float,
        checkin_after_seconds: float,
        *,
        owner_user_id: str = "",
    ) -> list[dict[str, Any]]:
        """Return recipients that have not been checked in since their last message."""

        cutoff = now - max(1, float(checkin_after_seconds))
        parameters: list[Any] = [cutoff]
        owner_filter = ""
        if owner_user_id:
            owner_filter = "AND user_id = ?"
            parameters.append(owner_user_id)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM recipients
                WHERE last_inbound_at <= ?
                  AND (
                    last_checkin_at IS NULL
                    OR last_checkin_at < last_inbound_at
                  )
                  {owner_filter}
                ORDER BY last_inbound_at
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_checkin_sent(self, user_id: str, sent_at: float) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE recipients
                SET last_checkin_at = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (sent_at, sent_at, user_id),
            )

    def create_reminder(
        self,
        user_id: str,
        content: str,
        run_at: float,
        *,
        timezone_name: str,
        repeat_kind: str,
        schedule_args: dict[str, Any] | None = None,
        action_kind: str = "message",
        action_args: dict[str, Any] | None = None,
    ) -> int:
        now = _now_timestamp()
        serialized_args = json.dumps(action_args or {}, ensure_ascii=False)
        serialized_schedule = json.dumps(schedule_args or {}, ensure_ascii=False)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reminders (
                    user_id, content, run_at, timezone, repeat_kind,
                    schedule_args, action_kind, action_args, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    user_id,
                    content,
                    run_at,
                    timezone_name,
                    repeat_kind,
                    serialized_schedule,
                    action_kind,
                    serialized_args,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def list_reminders(self, user_id: str) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM reminders
                WHERE user_id = ? AND status IN ({placeholders})
                ORDER BY run_at, id
                """,
                (user_id, *ACTIVE_STATUSES),
            ).fetchall()
        return [dict(row) for row in rows]

    def cancel_reminder(self, user_id: str, reminder_id: int) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders
                SET status = 'cancelled', last_error = NULL
                WHERE id = ? AND user_id = ?
                  AND status IN ('pending', 'waiting_reactivation')
                """,
                (reminder_id, user_id),
            )
            return cursor.rowcount > 0

    def claim_due(self, now: float, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM reminders
                WHERE status = 'pending' AND run_at <= ?
                ORDER BY run_at, id
                LIMIT ?
                """,
                (now, max(1, limit)),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"UPDATE reminders SET status = 'sending' WHERE id IN ({placeholders})",
                    ids,
                )
            return [dict(row) for row in rows]

    def release_claimed(self, reminder_ids: Iterable[int]) -> None:
        ids = [int(value) for value in reminder_ids]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connection() as connection:
            connection.execute(
                f"UPDATE reminders SET status = 'pending' WHERE id IN ({placeholders})",
                ids,
            )

    def mark_waiting(self, reminder_ids: Iterable[int], reason: str) -> None:
        ids = [int(value) for value in reminder_ids]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connection() as connection:
            connection.execute(
                f"""
                UPDATE reminders
                SET status = 'waiting_reactivation', last_error = ?
                WHERE id IN ({placeholders})
                """,
                (reason, *ids),
            )

    def mark_failed(self, reminder_ids: Iterable[int], error: str) -> None:
        ids = [int(value) for value in reminder_ids]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._connection() as connection:
            connection.execute(
                f"""
                UPDATE reminders
                SET status = 'failed', last_error = ?
                WHERE id IN ({placeholders})
                """,
                (error[:1000], *ids),
            )

    def mark_delivered(self, reminders: Iterable[dict[str, Any]], sent_at: float) -> None:
        with self._lock, self._connection() as connection:
            for reminder in reminders:
                reminder_id = int(reminder["id"])
                if reminder["repeat_kind"] == "daily":
                    next_run = _next_daily_timestamp(
                        float(reminder["run_at"]), sent_at, str(reminder["timezone"])
                    )
                    connection.execute(
                        """
                        UPDATE reminders
                        SET status = 'pending', run_at = ?, sent_at = ?, last_error = NULL
                        WHERE id = ?
                        """,
                        (next_run, sent_at, reminder_id),
                    )
                elif reminder["repeat_kind"] == "weekly":
                    next_run = _next_weekly_timestamp(
                        float(reminder["run_at"]),
                        sent_at,
                        str(reminder["timezone"]),
                        str(reminder.get("schedule_args") or "{}"),
                    )
                    connection.execute(
                        """
                        UPDATE reminders
                        SET status = 'pending', run_at = ?, sent_at = ?, last_error = NULL
                        WHERE id = ?
                        """,
                        (next_run, sent_at, reminder_id),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE reminders
                        SET status = 'sent', sent_at = ?, last_error = NULL
                        WHERE id = ?
                        """,
                        (sent_at, reminder_id),
                    )

    def reactivate_waiting(self, user_id: str) -> int:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders
                SET status = 'pending', last_error = NULL
                WHERE user_id = ? AND status = 'waiting_reactivation'
                """,
                (user_id,),
            )
            return cursor.rowcount


def list_reminder_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_current_time",
                "description": "获取提醒系统当前日期、时间和时区。处理今晚、明早等绝对时间前应先调用。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_reminder",
                "description": (
                    "创建单次、每日或每周指定星期提醒。相对时间优先传 delay_minutes；绝对时间传带日期的 ISO 8601，"
                    "每日提醒的 run_at 可传 HH:MM；每周提醒传 repeat=weekly、weekdays 和 HH:MM。"
                    "触发时只发送保存的文本，不会动态调用天气或联网工具。"
                    "不要在未调用工具时声称已经创建。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "提醒时发送的内容"},
                        "run_at": {
                            "type": "string",
                            "description": "绝对时间，如 2026-08-16T22:00:00+08:00；每日或每周可用 08:00",
                        },
                        "delay_minutes": {
                            "type": "number",
                            "description": "从现在起延迟多少分钟，和 run_at 二选一",
                        },
                        "repeat": {
                            "type": "string",
                            "enum": ["once", "daily", "weekly"],
                            "default": "once",
                        },
                        "weekdays": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 1, "maximum": 7},
                            "uniqueItems": True,
                            "description": "每周任务必填；1代表周一，7代表周日，例如周一和周五传 [1, 5]",
                        },
                    },
                    "required": ["content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_weather_schedule",
                "description": (
                    "创建真正的定时天气任务。任务触发时才调用和风天气获取最新实况和预报，"
                    "然后主动发送；不要用普通 create_reminder 代替天气任务。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "城市、区县或地点，如河南省邓州市",
                        },
                        "forecast_days": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 7,
                            "default": 3,
                        },
                        "run_at": {
                            "type": "string",
                            "description": "绝对时间；每日或每周任务可用 HH:MM",
                        },
                        "delay_minutes": {
                            "type": "number",
                            "description": "从现在起延迟多少分钟，和 run_at 二选一",
                        },
                        "repeat": {
                            "type": "string",
                            "enum": ["once", "daily", "weekly"],
                            "default": "once",
                        },
                        "weekdays": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 1, "maximum": 7},
                            "uniqueItems": True,
                            "description": "每周天气任务必填；1代表周一，7代表周日",
                        },
                    },
                    "required": ["location"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_reminders",
                "description": "列出当前用户尚未完成或正在等待重新激活的提醒。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cancel_reminder",
                "description": "根据提醒编号取消当前用户的提醒。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reminder_id": {"type": "integer", "minimum": 1}
                    },
                    "required": ["reminder_id"],
                },
            },
        },
    ]


class ReminderTools:
    def __init__(
        self,
        store: ReminderStore,
        *,
        timezone_name: str = "Asia/Shanghai",
        owner_user_id: str = "",
    ) -> None:
        self.store = store
        self.timezone_name = timezone_name
        self.zone = _timezone(timezone_name)
        self.owner_user_id = owner_user_id

    def call(self, user_id: str, name: str, arguments: dict[str, Any]) -> str:
        try:
            if self.owner_user_id and user_id != self.owner_user_id:
                raise ValueError("定时提醒只允许机器人绑定者使用")
            if name == "get_current_time":
                result = self._current_time()
            elif name == "create_reminder":
                result = self._create(user_id, arguments)
            elif name == "create_weather_schedule":
                result = self._create_weather(user_id, arguments)
            elif name == "list_reminders":
                result = self._list(user_id)
            elif name == "cancel_reminder":
                result = self._cancel(user_id, arguments)
            else:
                raise ValueError(f"未知提醒工具：{name}")
            return json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError, sqlite3.Error) as error:
            return json.dumps({"error": str(error)}, ensure_ascii=False)

    def _current_time(self) -> dict[str, Any]:
        now = datetime.now(self.zone)
        return {
            "timezone": self.timezone_name,
            "current_time": now.isoformat(timespec="seconds"),
        }

    def _create(self, user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        content = str(arguments.get("content") or "").strip()
        if not content:
            raise ValueError("提醒内容不能为空")
        if len(content) > 600:
            raise ValueError("单条提醒内容不能超过600个字符")

        repeat_kind, run_at, schedule_args = self._resolve_schedule(arguments)
        reminder_id = self.store.create_reminder(
            user_id,
            content,
            run_at.timestamp(),
            timezone_name=self.timezone_name,
            repeat_kind=repeat_kind,
            schedule_args=schedule_args,
        )
        return {
            "ok": True,
            "reminder_id": reminder_id,
            "type": "message",
            "content": content,
            "run_at": run_at.isoformat(timespec="seconds"),
            "repeat": repeat_kind,
            "weekdays": schedule_args.get("weekdays"),
            "note": "实际发送受微信24小时会话窗口和10次下发额度限制",
        }

    def _create_weather(
        self, user_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        location = str(arguments.get("location") or "").strip()
        if len(location) < 2:
            raise ValueError("天气任务需要至少两个字符的地点名称")
        forecast_days = max(1, min(int(arguments.get("forecast_days") or 3), 7))
        repeat_kind, run_at, schedule_args = self._resolve_schedule(arguments)
        content = f"查询{location}天气（未来{forecast_days}天）"
        reminder_id = self.store.create_reminder(
            user_id,
            content,
            run_at.timestamp(),
            timezone_name=self.timezone_name,
            repeat_kind=repeat_kind,
            schedule_args=schedule_args,
            action_kind="weather",
            action_args={"location": location, "forecast_days": forecast_days},
        )
        return {
            "ok": True,
            "reminder_id": reminder_id,
            "type": "weather",
            "location": location,
            "forecast_days": forecast_days,
            "run_at": run_at.isoformat(timespec="seconds"),
            "repeat": repeat_kind,
            "weekdays": schedule_args.get("weekdays"),
            "note": "到期时调用和风天气查询，不是发送固定提醒文字",
        }

    def _resolve_schedule(
        self, arguments: dict[str, Any]
    ) -> tuple[str, datetime, dict[str, Any]]:
        repeat_kind = str(arguments.get("repeat") or "once").strip().lower()
        if repeat_kind not in {"once", "daily", "weekly"}:
            raise ValueError("repeat 只允许 once、daily 或 weekly")
        now = datetime.now(self.zone)
        delay_value = arguments.get("delay_minutes")
        run_at_text = str(arguments.get("run_at") or "").strip()
        schedule_args: dict[str, Any] = {}
        if repeat_kind == "weekly":
            if delay_value is not None:
                raise ValueError("每周提醒请使用 run_at=HH:MM，不能使用 delay_minutes")
            weekdays = _normalize_weekdays(arguments.get("weekdays"))
            if not re.fullmatch(r"\d{1,2}:\d{2}", run_at_text):
                raise ValueError("每周提醒的 run_at 必须使用 HH:MM")
            hour_text, minute_text = run_at_text.split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError("每周提醒时间必须是有效的 HH:MM")
            run_at = _next_weekly_datetime(now, weekdays, hour, minute)
            schedule_args = {"weekdays": weekdays}
        elif delay_value is not None:
            delay_minutes = float(delay_value)
            if delay_minutes <= 0:
                raise ValueError("delay_minutes 必须大于0")
            run_at = now + timedelta(minutes=delay_minutes)
        elif repeat_kind == "daily" and re.fullmatch(r"\d{1,2}:\d{2}", run_at_text):
            hour_text, minute_text = run_at_text.split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError("每日提醒时间必须是有效的 HH:MM")
            run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run_at <= now:
                run_at += timedelta(days=1)
        else:
            if not run_at_text:
                raise ValueError("请提供 run_at 或 delay_minutes")
            normalized = run_at_text[:-1] + "+00:00" if run_at_text.endswith("Z") else run_at_text
            try:
                run_at = datetime.fromisoformat(normalized)
            except ValueError as error:
                raise ValueError("run_at 需要使用 ISO 8601 时间格式") from error
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=self.zone)
            run_at = run_at.astimezone(self.zone)

        if run_at <= now:
            if repeat_kind == "daily":
                while run_at <= now:
                    run_at += timedelta(days=1)
            else:
                raise ValueError("单次提醒时间必须晚于当前时间")

        return repeat_kind, run_at, schedule_args

    def _list(self, user_id: str) -> dict[str, Any]:
        rows = self.store.list_reminders(user_id)
        reminders = []
        for row in rows:
            local_time = datetime.fromtimestamp(float(row["run_at"]), UTC).astimezone(
                _timezone(str(row["timezone"]))
            )
            schedule_text = local_time.strftime("%Y-%m-%d %H:%M")
            weekdays: list[int] | None = None
            if row["repeat_kind"] == "daily":
                schedule_text = f"每天 {local_time:%H:%M}"
            elif row["repeat_kind"] == "weekly":
                try:
                    schedule_data = json.loads(str(row.get("schedule_args") or "{}"))
                    weekdays = _normalize_weekdays(schedule_data.get("weekdays"))
                    labels = "、".join(WEEKDAY_LABELS[value] for value in weekdays)
                    schedule_text = f"每{labels} {local_time:%H:%M}"
                except (json.JSONDecodeError, ValueError):
                    schedule_text = f"每周（调度参数异常）{local_time:%H:%M}"
            reminders.append(
                {
                    "id": int(row["id"]),
                    "content": row["content"],
                    "type": row["action_kind"],
                    "type_label": (
                        "天气侦察任务"
                        if row["action_kind"] == "weather"
                        else "日常提醒任务"
                    ),
                    "run_at": local_time.isoformat(timespec="seconds"),
                    "schedule_text": schedule_text,
                    "repeat": row["repeat_kind"],
                    "weekdays": weekdays,
                    "status": row["status"],
                }
            )
        return {
            "count": len(reminders),
            "reminders": reminders,
            "presentation_hint": (
                "准确保留任务数量、编号、内容、时间和类型，并以安洁莉娜明亮、可靠的"
                "信使助理口吻自然汇报；不要机械复读字段名，不要声称查询到结果中不存在的信息。"
            ),
        }

    def _cancel(self, user_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        reminder_id = int(arguments.get("reminder_id") or 0)
        if reminder_id <= 0:
            raise ValueError("reminder_id 必须是正整数")
        cancelled = self.store.cancel_reminder(user_id, reminder_id)
        return {"ok": cancelled, "reminder_id": reminder_id}


def execute_reminder_action(
    reminder: dict[str, Any],
    weather_caller: Callable[[str, dict[str, Any]], str],
) -> str:
    action_kind = str(reminder.get("action_kind") or "message")
    if action_kind == "message":
        return str(reminder.get("content") or "").strip()
    if action_kind != "weather":
        raise ValueError(f"未知定时任务类型：{action_kind}")

    try:
        arguments = json.loads(str(reminder.get("action_args") or "{}"))
    except json.JSONDecodeError as error:
        raise ValueError("天气任务参数损坏，无法解析") from error
    if not isinstance(arguments, dict):
        raise ValueError("天气任务参数必须是 JSON 对象")
    location = str(arguments.get("location") or "").strip()
    forecast_days = max(1, min(int(arguments.get("forecast_days") or 3), 7))
    raw_result = weather_caller(
        "get_weather",
        {"location": location, "forecast_days": forecast_days},
    )
    try:
        result = json.loads(raw_result)
    except json.JSONDecodeError as error:
        raise ValueError("天气 MCP 返回了无法解析的结果") from error
    if not isinstance(result, dict):
        raise ValueError("天气 MCP 返回格式不正确")
    if result.get("error"):
        raise RuntimeError(f"和风天气查询失败：{result['error']}")
    return format_weather_message(result)


def format_weather_message(result: dict[str, Any]) -> str:
    location = str(result.get("resolved_location") or result.get("query") or "目标地点")
    current = result.get("current") or {}
    condition = current.get("condition") or "未知"
    temperature = current.get("temperature_c")
    apparent = current.get("apparent_temperature_c")
    humidity = current.get("relative_humidity_percent")
    wind_direction = current.get("wind_direction") or ""
    wind_scale = current.get("wind_scale")

    current_parts = [f"{condition}"]
    if temperature is not None:
        current_parts.append(f"{temperature}℃")
    if apparent is not None:
        current_parts.append(f"体感{apparent}℃")
    if humidity is not None:
        current_parts.append(f"湿度{humidity}%")
    if wind_direction or wind_scale is not None:
        current_parts.append(f"{wind_direction}{wind_scale or ''}级")

    lines = [f"🌤 {location}天气", "当前：" + "，".join(current_parts)]
    forecasts = result.get("forecast") or []
    if forecasts:
        lines.append("预报：")
        for day in forecasts:
            date = day.get("date") or "日期未知"
            day_condition = day.get("condition") or "未知"
            low = day.get("temperature_min_c")
            high = day.get("temperature_max_c")
            temperature_range = ""
            if low is not None and high is not None:
                temperature_range = f"，{low}～{high}℃"
            precipitation = day.get("precipitation_sum_mm")
            rain_text = ""
            if precipitation not in (None, 0, 0.0):
                rain_text = f"，降水{precipitation}mm"
            lines.append(f"{date}：{day_condition}{temperature_range}{rain_text}")
    update_time = result.get("provider_update_time")
    if update_time:
        lines.append(f"更新时间：{update_time}")
    source_url = result.get("source_url")
    lines.append(f"来源：和风天气{f' {source_url}' if source_url else ''}")
    return "\n".join(lines)


class ReminderScheduler:
    """Claim due reminders and deliver one quota-conscious batch at a time."""

    def __init__(
        self,
        store: ReminderStore,
        sender: Callable[[str, str, str], int],
        chat_log: Any,
        stop_event: threading.Event,
        send_lock: threading.RLock,
        *,
        action_executor: Callable[[dict[str, Any]], str] | None = None,
        active_hours: float = 24,
        outbound_limit: int = 10,
        check_interval: float = 1,
        max_message_chars: int = 1800,
        checkin_enabled: bool = False,
        checkin_after_hours: float = 23,
        checkin_messages: Iterable[str] = (),
        reminder_intros: Iterable[str] = (),
        context_recorder: Callable[[str, str], None] | None = None,
        owner_user_id: str = "",
    ) -> None:
        self.store = store
        self.sender = sender
        self.chat_log = chat_log
        self.stop_event = stop_event
        self.send_lock = send_lock
        self.action_executor = action_executor
        self.active_seconds = max(1, float(active_hours)) * 3600
        self.outbound_limit = max(1, int(outbound_limit))
        self.check_interval = max(0.2, float(check_interval))
        self.max_message_chars = max(100, int(max_message_chars))
        self.checkin_enabled = bool(checkin_enabled)
        requested_checkin_seconds = max(1, float(checkin_after_hours)) * 3600
        self.checkin_after_seconds = min(
            requested_checkin_seconds,
            max(1, self.active_seconds - 60),
        )
        self.checkin_messages = tuple(
            text.strip() for text in checkin_messages if text.strip()
        )
        self.reminder_intros = tuple(
            text.strip() for text in reminder_intros if text.strip()
        )
        self.context_recorder = context_recorder
        self.owner_user_id = owner_user_id
        self._checkin_retry_after: dict[str, float] = {}
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="reminder-scheduler",
        )
        self.thread.start()

    def join(self, timeout: float = 5) -> None:
        if self.thread is not None:
            self.thread.join(timeout)

    def _run(self) -> None:
        self.chat_log.system("SQLite 定时提醒调度器已启动")
        while not self.stop_event.is_set():
            try:
                due = self.store.claim_due(_now_timestamp())
                grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for reminder in due:
                    grouped[str(reminder["user_id"])].append(reminder)
                for user_id, reminders in grouped.items():
                    self._deliver_group(user_id, reminders)
                self._send_due_checkins()
            except Exception as error:
                self.chat_log.error(f"定时提醒调度异常：{error}")
            self.stop_event.wait(self.check_interval)
        self.chat_log.system("SQLite 定时提醒调度器已停止")

    def _send_due_checkins(self) -> None:
        """Send one non-model check-in for each inbound conversation window."""

        if not self.checkin_enabled or not self.checkin_messages:
            return
        now = _now_timestamp()
        recipients = self.store.due_checkin_recipients(
            now,
            self.checkin_after_seconds,
            owner_user_id=self.owner_user_id,
        )
        for candidate in recipients:
            user_id = str(candidate["user_id"])
            if self._checkin_retry_after.get(user_id, 0) > now:
                continue
            with self.send_lock:
                recipient = self.store.recipient(user_id)
                now = _now_timestamp()
                if recipient is None or not self._checkin_is_due(recipient, now):
                    continue
                reason = self._permission_reason(recipient, now)
                if reason:
                    continue
                text = random.choice(self.checkin_messages)
                try:
                    chunks = self.sender(
                        user_id,
                        str(recipient["context_token"]),
                        text,
                    )
                    self.store.record_outbound(user_id, chunks)
                    sent_at = _now_timestamp()
                    self.store.mark_checkin_sent(user_id, sent_at)
                    if self.context_recorder is not None:
                        self.context_recorder(user_id, text)
                    self.chat_log.assistant(f"[主动问候][{user_id}] {text}")
                    self._checkin_retry_after.pop(user_id, None)
                except Exception as error:
                    # Avoid retrying every scheduler tick when the channel is unavailable.
                    self._checkin_retry_after[user_id] = now + 15 * 60
                    self.chat_log.error(f"主动问候发送失败 [{user_id}]：{error}")

    def _checkin_is_due(self, recipient: dict[str, Any], now: float) -> bool:
        last_inbound = float(recipient["last_inbound_at"])
        last_checkin = recipient.get("last_checkin_at")
        return (
            now - last_inbound >= self.checkin_after_seconds
            and (last_checkin is None or float(last_checkin) < last_inbound)
        )

    def _deliver_group(self, user_id: str, reminders: list[dict[str, Any]]) -> None:
        ids = [int(item["id"]) for item in reminders]
        recipient = self.store.recipient(user_id)
        reason = self._permission_reason(recipient, _now_timestamp())
        if reason:
            self.store.mark_waiting(ids, reason)
            return

        prepared: list[dict[str, Any]] = []
        for reminder in reminders:
            try:
                delivery_text = (
                    self.action_executor(reminder)
                    if self.action_executor is not None
                    else str(reminder["content"])
                ).strip()
                if not delivery_text:
                    raise ValueError("定时任务生成了空消息")
                prepared_reminder = dict(reminder)
                prepared_reminder["delivery_text"] = delivery_text
                prepared.append(prepared_reminder)
            except Exception as error:
                self.store.mark_failed([int(reminder["id"])], str(error))
                self.chat_log.error(
                    f"定时任务执行失败 [{user_id}][{reminder['id']}]：{error}"
                )
        if not prepared:
            return

        with self.send_lock:
            recipient = self.store.recipient(user_id)
            now = _now_timestamp()
            reason = self._permission_reason(recipient, now)
            if reason:
                self.store.mark_waiting(
                    [int(item["id"]) for item in prepared], reason
                )
                return

            assert recipient is not None
            included, deferred, text = self._build_batch(prepared)
            self.store.release_claimed(int(item["id"]) for item in deferred)
            included_ids = [int(item["id"]) for item in included]
            try:
                chunks = self.sender(user_id, str(recipient["context_token"]), text)
                self.store.record_outbound(user_id, chunks)
                sent_at = _now_timestamp()
                self.store.mark_delivered(included, sent_at)
                if self.context_recorder is not None:
                    self.context_recorder(user_id, text)
                self.chat_log.assistant(f"[定时提醒][{user_id}] {text}")
            except Exception as error:
                error_text = str(error)
                if "ret=-2" in error_text or "rate limited" in error_text.lower():
                    self.store.mark_waiting(
                        included_ids, "微信拒绝主动下发，等待用户重新发消息激活"
                    )
                else:
                    self.store.mark_failed(included_ids, error_text)
                self.chat_log.error(f"定时提醒发送失败 [{user_id}]：{error}")

    def _permission_reason(
        self, recipient: dict[str, Any] | None, now: float
    ) -> str | None:
        if recipient is None:
            return "尚未收到用户消息，缺少发送上下文"
        age = now - float(recipient["last_inbound_at"])
        if age >= self.active_seconds:
            return "超过微信24小时主动下发窗口"
        if int(recipient["outbound_count"]) >= self.outbound_limit:
            return "已达到微信主动下发次数限制"
        return None

    def _build_batch(
        self, reminders: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        header = (
            random.choice(self.reminder_intros)
            if self.reminder_intros
            else "⏰ 定时提醒"
        )
        if len(reminders) == 1:
            reminder = reminders[0]
            content = str(reminder["delivery_text"]).strip()
            available = self.max_message_chars - len(header) - 2
            if len(content) > available:
                content = content[: max(1, available - 1)] + "…"
            return [reminder], [], f"{header}\n{content}"

        included: list[dict[str, Any]] = []
        lines: list[str] = []
        for reminder in reminders:
            line = f"{len(included) + 1}. {str(reminder['delivery_text']).strip()}"
            candidate = header + "\n" + "\n".join([*lines, line])
            if included and len(candidate) > self.max_message_chars:
                break
            if len(candidate) > self.max_message_chars:
                line = line[: self.max_message_chars - len(header) - 5] + "…"
            included.append(reminder)
            lines.append(line)
        deferred = reminders[len(included) :]
        return included, deferred, header + "\n" + "\n".join(lines)
