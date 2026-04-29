"""Discord 非依存のタイマー/ストップウォッチ。"""

from __future__ import annotations

import time
from dataclasses import dataclass


def format_duration_jp(seconds: int) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    if minutes <= 0:
        return f"{secs}秒"
    return f"{minutes}分{secs}秒"


@dataclass
class Stopwatch:
    started_at: float | None = None
    accumulated_seconds: float = 0.0

    def start(self) -> None:
        if self.started_at is None:
            self.started_at = time.monotonic()

    def stop(self) -> None:
        if self.started_at is None:
            return
        self.accumulated_seconds += time.monotonic() - self.started_at
        self.started_at = None

    def reset(self) -> None:
        self.started_at = None
        self.accumulated_seconds = 0.0

    def elapsed_seconds(self) -> int:
        if self.started_at is None:
            return max(0, int(self.accumulated_seconds))
        return max(0, int(self.accumulated_seconds + (time.monotonic() - self.started_at)))

    @property
    def running(self) -> bool:
        return self.started_at is not None


@dataclass
class CountdownTimer:
    total_seconds: int
    started_at: float | None = None

    def start(self) -> None:
        self.started_at = time.monotonic()

    def restart(self) -> None:
        self.start()

    def elapsed_seconds(self) -> int:
        if self.started_at is None:
            return 0
        return max(0, int(time.monotonic() - self.started_at))

    def remaining_seconds(self) -> int:
        return max(0, int(self.total_seconds) - self.elapsed_seconds())

    def is_done(self) -> bool:
        return self.remaining_seconds() <= 0
