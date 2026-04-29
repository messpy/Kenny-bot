"""Compatibility shim for spam guard types."""

from src.kennybot.features.spam import SpamGuard, SpamPolicy, UserViolationLevel

__all__ = ["SpamGuard", "SpamPolicy", "UserViolationLevel"]
