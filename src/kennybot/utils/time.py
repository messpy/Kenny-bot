"""日時関連の共通 utility。"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone


UTC = timezone.utc
JST = timezone(timedelta(hours=9))


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_jst() -> datetime:
    return datetime.now(JST)


def today_jst() -> date:
    return now_jst().date()


def monotonic_now() -> float:
    return time.monotonic()


def as_jst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def format_jst(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S JST") -> str:
    return as_jst(dt).strftime(fmt)


def isoformat_jst(*, timespec: str = "seconds") -> str:
    return now_jst().isoformat(timespec=timespec)
