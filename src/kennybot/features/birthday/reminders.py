from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from src.kennybot.storage.database import connect_database, resolve_database_config, sql_placeholders
from src.kennybot.utils.paths import RUNTIME_STATE_DIR


@dataclass(frozen=True)
class BirthdayReminderRecord:
    id: int
    guild_id: int
    channel_id: int
    display_name: str
    birthday_date: date
    notify_time: str
    user_id: int | None
    created_by_id: int
    last_notified_year: int | None
    active: bool
    created_at: str
    updated_at: str

    @property
    def birthday_month(self) -> int:
        return self.birthday_date.month

    @property
    def birthday_day(self) -> int:
        return self.birthday_date.day

    @property
    def notify_hour(self) -> int:
        return int(self.notify_time.split(":", 1)[0])

    @property
    def notify_minute(self) -> int:
        return int(self.notify_time.split(":", 1)[1])


class BirthdayReminderStore:
    def __init__(self, path: Path | None = None, *, backend: str | None = None) -> None:
        self.path = path or (RUNTIME_STATE_DIR / "birthday_reminders.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = resolve_database_config(self.path, backend=backend)
        self._ensure_schema()

    def _connect(self) -> Any:
        return connect_database(self._db)

    def _sql(self, sql: str) -> str:
        return sql_placeholders(sql, self._db)

    @staticmethod
    def _column_name_from_row(row: Any) -> str:
        if isinstance(row, dict):
            return str(row.get("COLUMN_NAME") or row.get("column_name") or row.get("name") or "")
        return str(row[0])

    def _ensure_schema(self) -> None:
        with closing(self._connect()) as conn:
            if self._db.backend == "sqlite":
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS birthday_reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER NOT NULL,
                        channel_id INTEGER NOT NULL,
                        display_name TEXT NOT NULL,
                        birthday_date TEXT NOT NULL,
                        notify_time TEXT NOT NULL DEFAULT '12:00',
                        user_id INTEGER NULL,
                        created_by_id INTEGER NOT NULL,
                        last_notified_year INTEGER NULL,
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_birthday_reminders_guild_active "
                    "ON birthday_reminders (guild_id, active, birthday_date)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_birthday_reminders_user "
                    "ON birthday_reminders (guild_id, user_id)"
                )
                columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(birthday_reminders)").fetchall()}
                if "notify_time" not in columns:
                    conn.execute("ALTER TABLE birthday_reminders ADD COLUMN notify_time TEXT NOT NULL DEFAULT '12:00'")
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS birthday_reminders (
                            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                            guild_id BIGINT NOT NULL,
                            channel_id BIGINT NOT NULL,
                            display_name LONGTEXT NOT NULL,
                            birthday_date VARCHAR(10) NOT NULL,
                            notify_time VARCHAR(5) NOT NULL DEFAULT '12:00',
                            user_id BIGINT NULL,
                            created_by_id BIGINT NOT NULL,
                            last_notified_year INT NULL,
                            active TINYINT(1) NOT NULL DEFAULT 1,
                            created_at VARCHAR(64) NOT NULL,
                            updated_at VARCHAR(64) NOT NULL,
                            INDEX idx_birthday_reminders_guild_active (guild_id, active, birthday_date),
                            INDEX idx_birthday_reminders_user (guild_id, user_id)
                        )
                        """
                    )
                    cur.execute(
                        """
                        SELECT COLUMN_NAME
                        FROM INFORMATION_SCHEMA.COLUMNS
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND TABLE_NAME = 'birthday_reminders'
                        """
                    )
                    columns = {self._column_name_from_row(row) for row in cur.fetchall()}
                    if "notify_time" not in columns:
                        cur.execute(
                            "ALTER TABLE birthday_reminders ADD COLUMN notify_time VARCHAR(5) NOT NULL DEFAULT '12:00'"
                        )

    @staticmethod
    def _row_to_record(row: Any) -> BirthdayReminderRecord:
        raw_date = str(row["birthday_date"])
        return BirthdayReminderRecord(
            id=int(row["id"]),
            guild_id=int(row["guild_id"]),
            channel_id=int(row["channel_id"]),
            display_name=str(row["display_name"]),
            birthday_date=date.fromisoformat(raw_date),
            notify_time=str(row["notify_time"]) if "notify_time" in row.keys() else "12:00",
            user_id=None if row["user_id"] is None else int(row["user_id"]),
            created_by_id=int(row["created_by_id"]),
            last_notified_year=None if row["last_notified_year"] is None else int(row["last_notified_year"]),
            active=bool(row["active"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _fetch_one(self, sql: str, params: tuple[Any, ...]) -> BirthdayReminderRecord | None:
        with closing(self._connect()) as conn:
            cursor = conn.cursor()
            cursor.execute(self._sql(sql), params)
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def upsert_reminder(
        self,
        *,
        guild_id: int,
        channel_id: int,
        display_name: str,
        birthday_date: date,
        created_by_id: int,
        notify_time: str = "12:00",
        user_id: int | None = None,
        active: bool = True,
    ) -> BirthdayReminderRecord:
        now = datetime.now(timezone.utc).isoformat()
        birthday_iso = birthday_date.isoformat()
        display_name = display_name.strip() or "Unknown"
        notify_time = notify_time.strip() or "12:00"

        existing: BirthdayReminderRecord | None = None
        if user_id is not None:
            existing = self.get_by_user(guild_id=guild_id, user_id=user_id)
        if existing is None:
            existing = self.get_by_signature(
                guild_id=guild_id,
                channel_id=channel_id,
                display_name=display_name,
                birthday_date=birthday_date,
            )

        with closing(self._connect()) as conn:
            if existing is None:
                if self._db.backend == "sqlite":
                    cursor = conn.execute(
                        """
                        INSERT INTO birthday_reminders (
                            guild_id, channel_id, display_name, birthday_date, notify_time, user_id,
                            created_by_id, last_notified_year, active, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            int(guild_id),
                            int(channel_id),
                            display_name,
                            birthday_iso,
                            notify_time,
                            user_id,
                            int(created_by_id),
                            None,
                            1 if active else 0,
                            now,
                            now,
                        ),
                    )
                    reminder_id = int(cursor.lastrowid)
                else:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        INSERT INTO birthday_reminders (
                            guild_id, channel_id, display_name, birthday_date, notify_time, user_id,
                            created_by_id, last_notified_year, active, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            int(guild_id),
                            int(channel_id),
                            display_name,
                            birthday_iso,
                            notify_time,
                            user_id,
                            int(created_by_id),
                            None,
                            1 if active else 0,
                            now,
                            now,
                        ),
                    )
                    reminder_id = int(cursor.lastrowid)
            else:
                reminder_id = existing.id
                if self._db.backend == "sqlite":
                    conn.execute(
                        """
                        UPDATE birthday_reminders SET
                            channel_id = ?,
                            display_name = ?,
                            birthday_date = ?,
                            notify_time = ?,
                            user_id = ?,
                            created_by_id = ?,
                            active = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            int(channel_id),
                            display_name,
                            birthday_iso,
                            notify_time,
                            user_id,
                            int(created_by_id),
                            1 if active else 0,
                            now,
                            reminder_id,
                        ),
                    )
                else:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        UPDATE birthday_reminders SET
                            channel_id = %s,
                            display_name = %s,
                            birthday_date = %s,
                            notify_time = %s,
                            user_id = %s,
                            created_by_id = %s,
                            active = %s,
                            updated_at = %s
                        WHERE id = %s
                        """,
                        (
                            int(channel_id),
                            display_name,
                            birthday_iso,
                            notify_time,
                            user_id,
                            int(created_by_id),
                            1 if active else 0,
                            now,
                            reminder_id,
                        ),
                    )
            conn.commit()

        record = self.get_by_id(guild_id=guild_id, reminder_id=reminder_id)
        if record is None:
            raise RuntimeError("failed to load saved birthday reminder")
        return record

    def get_by_id(self, *, guild_id: int, reminder_id: int) -> BirthdayReminderRecord | None:
        return self._fetch_one(
            "SELECT * FROM birthday_reminders WHERE guild_id = ? AND id = ?",
            (int(guild_id), int(reminder_id)),
        )

    def get_by_user(self, *, guild_id: int, user_id: int) -> BirthdayReminderRecord | None:
        return self._fetch_one(
            "SELECT * FROM birthday_reminders WHERE guild_id = ? AND user_id = ? AND active = 1 ORDER BY updated_at DESC LIMIT 1",
            (int(guild_id), int(user_id)),
        )

    def get_by_signature(
        self,
        *,
        guild_id: int,
        channel_id: int,
        display_name: str,
        birthday_date: date,
    ) -> BirthdayReminderRecord | None:
        sql = (
            "SELECT * FROM birthday_reminders WHERE guild_id = ? AND channel_id = ? "
            "AND birthday_date = ? AND LOWER(display_name) = LOWER(?) AND active = 1 "
            "ORDER BY updated_at DESC LIMIT 1"
        )
        return self._fetch_one(
            sql,
            (int(guild_id), int(channel_id), birthday_date.isoformat(), display_name),
        )

    def remove(self, *, guild_id: int, reminder_id: int) -> bool:
        with closing(self._connect()) as conn:
            if self._db.backend == "sqlite":
                cursor = conn.execute(
                    "DELETE FROM birthday_reminders WHERE guild_id = ? AND id = ?",
                    (int(guild_id), int(reminder_id)),
                )
                deleted = cursor.rowcount
            else:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM birthday_reminders WHERE guild_id = %s AND id = %s",
                    (int(guild_id), int(reminder_id)),
                )
                deleted = cursor.rowcount
            conn.commit()
        return bool(deleted)

    def list_for_guild(self, guild_id: int) -> list[BirthdayReminderRecord]:
        with closing(self._connect()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                self._sql("SELECT * FROM birthday_reminders WHERE guild_id = ? ORDER BY birthday_date ASC, id ASC"),
                (int(guild_id),),
            )
            rows = cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_due_for_now(self, current: datetime) -> list[BirthdayReminderRecord]:
        with closing(self._connect()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                self._sql("SELECT * FROM birthday_reminders WHERE active = 1 ORDER BY guild_id ASC, birthday_date ASC, id ASC"),
            )
            rows = cursor.fetchall()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        today = current.date()
        due: list[BirthdayReminderRecord] = []
        for row in rows:
            record = self._row_to_record(row)
            if record.birthday_month != today.month or record.birthday_day != today.day:
                continue
            if record.last_notified_year == today.year:
                continue
            notify_dt = datetime(
                today.year,
                today.month,
                today.day,
                record.notify_hour,
                record.notify_minute,
                tzinfo=current.tzinfo,
            )
            if current < notify_dt:
                continue
            if current > notify_dt + timedelta(hours=1):
                continue
            due.append(record)
        return due

    def list_due_for_today(self, today: date) -> list[BirthdayReminderRecord]:
        current = datetime(today.year, today.month, today.day, 12, 0, tzinfo=timezone.utc)
        return self.list_due_for_now(current)

    def mark_notified(self, *, reminder_id: int, year: int) -> None:
        from datetime import timezone

        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as conn:
            if self._db.backend == "sqlite":
                conn.execute(
                    "UPDATE birthday_reminders SET last_notified_year = ?, updated_at = ? WHERE id = ?",
                    (int(year), now, int(reminder_id)),
                )
            else:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE birthday_reminders SET last_notified_year = %s, updated_at = %s WHERE id = %s",
                    (int(year), now, int(reminder_id)),
                )
            conn.commit()
