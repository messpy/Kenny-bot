"""スパム制御機能。"""

from .guard import SpamGuard, SpamPolicy, UserViolationLevel

__all__ = ["SpamGuard", "SpamPolicy", "UserViolationLevel"]
