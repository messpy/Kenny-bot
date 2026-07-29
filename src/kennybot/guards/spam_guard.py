"""Compatibility shim for spam guard types."""

from src.kennybot.features.spam import (
    EveryoneCrossChannelViolation,
    EveryoneMentionEvent,
    EveryoneMentionViolation,
    SpamGuard,
    SpamPolicy,
    UserViolationLevel,
)

__all__ = [
    "EveryoneCrossChannelViolation",
    "EveryoneMentionEvent",
    "EveryoneMentionViolation",
    "SpamGuard",
    "SpamPolicy",
    "UserViolationLevel",
]
