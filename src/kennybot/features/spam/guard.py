"""スパム制御の純粋ロジック。"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class SpamPolicy:
    max_msgs: int = 5
    per_seconds: float = 8.0
    max_ai_calls: int = 2
    ai_per_seconds: float = 20.0
    dup_window_seconds: float = 12.0
    warn_cooldown_seconds: float = 20.0
    everyone_cross_channel_window_seconds: float = 1.0


@dataclass
class UserViolationLevel:
    user_id: int
    guild_id: int
    violation_count: int = 0
    last_violation_time: float = 0.0
    current_level: str = "none"
    muted_until: float = 0.0
    last_reset: float = field(default_factory=time.time)

    def get_level(self) -> str:
        if self.muted_until > time.time():
            return "muted"
        return self.current_level

    def reset(self) -> None:
        self.violation_count = 0
        self.current_level = "none"
        self.muted_until = 0.0
        self.last_reset = time.time()


@dataclass(frozen=True)
class EveryoneMentionEvent:
    guild_id: int
    user_id: int
    channel_id: int
    message_id: int
    timestamp: float


@dataclass(frozen=True)
class EveryoneCrossChannelViolation:
    guild_id: int
    user_id: int
    events: tuple[EveryoneMentionEvent, ...]


class SpamGuard:
    """Discord に依存しないスパム判定。"""

    def __init__(self, policy: SpamPolicy):
        self.p = policy
        self._msg_times: dict[int, Deque[float]] = {}
        self._ai_times: dict[int, Deque[float]] = {}
        self._last_text: dict[int, tuple[str, float]] = {}
        self._last_warn: dict[int, float] = {}
        self._violations: dict[tuple[int, int], UserViolationLevel] = {}
        self._everyone_mentions: dict[tuple[int, int], Deque[EveryoneMentionEvent]] = {}

    def _allow(
        self,
        store: dict[int, Deque[float]],
        user_id: int,
        limit: int,
        window: float,
    ) -> bool:
        now = time.time()
        dq = store.get(user_id)
        if dq is None:
            dq = deque()
            store[user_id] = dq

        while dq and (now - dq[0]) > window:
            dq.popleft()

        if len(dq) >= limit:
            return False

        dq.append(now)
        return True

    def is_duplicate_spam(self, user_id: int, text: str) -> bool:
        now = time.time()
        previous = self._last_text.get(user_id)
        self._last_text[user_id] = (text, now)
        if not previous:
            return False
        prev_text, prev_ts = previous
        return bool(text and text == prev_text and (now - prev_ts) <= self.p.dup_window_seconds)

    def allow_message(self, user_id: int, text: str) -> bool:
        if self.is_duplicate_spam(user_id, text):
            return False
        return self._allow(self._msg_times, user_id, self.p.max_msgs, self.p.per_seconds)

    def allow_ai(self, user_id: int) -> bool:
        return self._allow(self._ai_times, user_id, self.p.max_ai_calls, self.p.ai_per_seconds)

    def record_everyone_mention(
        self,
        *,
        guild_id: int,
        user_id: int,
        channel_id: int,
        message_id: int,
        now: float | None = None,
    ) -> EveryoneCrossChannelViolation | None:
        timestamp = time.time() if now is None else now
        window = self.p.everyone_cross_channel_window_seconds
        key = (guild_id, user_id)
        dq = self._everyone_mentions.get(key)
        if dq is None:
            dq = deque()
            self._everyone_mentions[key] = dq

        while dq and (timestamp - dq[0].timestamp) > window:
            dq.popleft()

        event = EveryoneMentionEvent(
            guild_id=guild_id,
            user_id=user_id,
            channel_id=channel_id,
            message_id=message_id,
            timestamp=timestamp,
        )
        dq.append(event)

        channels = {item.channel_id for item in dq}
        if len(channels) < 2:
            return None

        events = tuple(dq)
        dq.clear()
        return EveryoneCrossChannelViolation(
            guild_id=guild_id,
            user_id=user_id,
            events=events,
        )

    def ai_retry_after(self, user_id: int) -> float:
        now = time.time()
        dq = self._ai_times.get(user_id)
        if not dq:
            return 0.0
        while dq and (now - dq[0]) > self.p.ai_per_seconds:
            dq.popleft()
        if len(dq) < self.p.max_ai_calls:
            return 0.0
        return max(0.0, self.p.ai_per_seconds - (now - dq[0]))

    def should_warn(self, user_id: int) -> bool:
        now = time.time()
        last = self._last_warn.get(user_id, 0.0)
        if (now - last) < self.p.warn_cooldown_seconds:
            return False
        self._last_warn[user_id] = now
        return True

    def get_violation(self, user_id: int, guild_id: int) -> UserViolationLevel:
        key = (user_id, guild_id)
        if key not in self._violations:
            self._violations[key] = UserViolationLevel(user_id, guild_id)
        return self._violations[key]

    def add_violation(self, user_id: int, guild_id: int) -> UserViolationLevel:
        violation = self.get_violation(user_id, guild_id)
        violation.violation_count += 1
        violation.last_violation_time = time.time()
        if violation.violation_count >= 5:
            violation.current_level = "ban"
        elif violation.violation_count >= 4:
            violation.current_level = "kick"
        elif violation.violation_count >= 2:
            violation.current_level = "mute"
        else:
            violation.current_level = "warning"
        return violation

    def reset_violation(self, user_id: int, guild_id: int) -> None:
        self.get_violation(user_id, guild_id).reset()

    def get_all_violations(self) -> dict[tuple[int, int], UserViolationLevel]:
        return self._violations
