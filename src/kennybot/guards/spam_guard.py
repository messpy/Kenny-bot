"""Compatibility shim for spam guard types."""

from src.kennybot.features.spam import (
    EveryoneCrossChannelViolation,
    EveryoneMentionEvent,
    SpamGuard,
    SpamPolicy,
    UserViolationLevel,
)

__all__ = [
    "EveryoneCrossChannelViolation",
    "EveryoneMentionEvent",
    "SpamGuard",
    "SpamPolicy",
    "UserViolationLevel",
]
