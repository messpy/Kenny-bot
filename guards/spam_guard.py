# guards/spam_guard.py
# スパム対策（SpamPolicy, SpamGuard）

import time
from dataclasses import dataclass, field
from typing import Dict, Deque, Tuple
from collections import deque


@dataclass
class SpamPolicy:
    """スパム対策ポリシー"""
    # 通常メッセージレート
    max_msgs: int = 5
    per_seconds: float = 8.0

    # AI利用レート（高コスト）
    max_ai_calls: int = 2
    ai_per_seconds: float = 20.0

    # 同一文の連投を抑止
    dup_window_seconds: float = 12.0

    # 警告メッセージを出す間隔（出しすぎ防止）
    warn_cooldown_seconds: float = 20.0


@dataclass
class UserViolationLevel:
    """ユーザーの違反レベル管理"""
    user_id: int
    guild_id: int
    violation_count: int = 0  # 違反回数
    last_violation_time: float = 0.0
    current_level: str = "none"  # none -> warning -> mute -> kick -> ban
    muted_until: float = 0.0
    last_reset: float = field(default_factory=time.time)

    def get_level(self) -> str:
        """現在のレベルを取得"""
        if self.muted_until > time.time():
            return "muted"
        return self.current_level

    def reset(self) -> None:
        """違反カウントをリセット"""
        self.violation_count = 0
        self.current_level = "none"
        self.muted_until = 0.0
        self.last_reset = time.time()


class SpamGuard:
    """ユーザー単位のスパム検出と違反レベル管理"""

    def __init__(self, policy: SpamPolicy):
        self.p = policy
        self._msg_times: Dict[int, Deque[float]] = {}
        self._ai_times: Dict[int, Deque[float]] = {}
        self._last_text: Dict[int, Tuple[str, float]] = {}
        self._last_warn: Dict[int, float] = {}
        # 違反レベル管理（(user_id, guild_id) -> UserViolationLevel）
        self._violations: Dict[Tuple[int, int], UserViolationLevel] = {}

    def _allow(
        self,
        store: Dict[int, Deque[float]],
        user_id: int,
        limit: int,
        window: float,
    ) -> bool:
        """レート制限チェック（内部用）"""
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
        """同一文の連投か判定"""
        now = time.time()
        prev = self._last_text.get(user_id)
        self._last_text[user_id] = (text, now)
        if not prev:
            return False
        prev_text, prev_ts = prev
        if text and text == prev_text and (now - prev_ts) <= self.p.dup_window_seconds:
            return True
        return False

    def allow_message(self, user_id: int, text: str) -> bool:
        """通常メッセージを許可するか"""
        if self.is_duplicate_spam(user_id, text):
            return False
        return self._allow(self._msg_times, user_id, self.p.max_msgs, self.p.per_seconds)

    def allow_ai(self, user_id: int) -> bool:
        """AI呼び出しを許可するか"""
        return self._allow(self._ai_times, user_id, self.p.max_ai_calls, self.p.ai_per_seconds)

    def should_warn(self, user_id: int) -> bool:
        """警告メッセージを送出するか（cooldown付き）"""
        now = time.time()
        last = self._last_warn.get(user_id, 0.0)
        if (now - last) < self.p.warn_cooldown_seconds:
            return False
        self._last_warn[user_id] = now
        return True
    # =====================================
    # 違反レベル管理
    # =====================================
    def get_violation(self, user_id: int, guild_id: int) -> UserViolationLevel:
        """ユーザーの違反レベルを取得（なければ新規作成）"""
        key = (user_id, guild_id)
        if key not in self._violations:
            self._violations[key] = UserViolationLevel(user_id, guild_id)
        return self._violations[key]

    def add_violation(self, user_id: int, guild_id: int) -> UserViolationLevel:
        """違反を記録し、レベルアップ"""
        violation = self.get_violation(user_id, guild_id)
        violation.violation_count += 1
        violation.last_violation_time = time.time()

        # 違反回数に基づてレベル更新
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
        """ユーザーの違反をリセット"""
        violation = self.get_violation(user_id, guild_id)
        violation.reset()

    def get_all_violations(self) -> Dict[Tuple[int, int], UserViolationLevel]:
        """すべての違反情報を取得"""
        return self._violations
