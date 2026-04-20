import atexit
import fcntl
from pathlib import Path
from typing import TextIO


class SingleInstanceError(RuntimeError):
    """Raised when another bot process already holds the runtime lock."""


_lock_handle: TextIO | None = None


def acquire_lock(lock_path: str | Path) -> None:
    """Acquire a non-blocking process lock and keep it for process lifetime."""
    global _lock_handle

    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise SingleInstanceError(f"lock already held: {path}") from exc

    handle.seek(0)
    handle.truncate()
    handle.write(f"{Path('/proc/self').resolve().name}\n")
    handle.flush()
    _lock_handle = handle
    atexit.register(release_lock)


def release_lock() -> None:
    global _lock_handle
    if _lock_handle is None:
        return

    try:
        fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
    finally:
        _lock_handle.close()
        _lock_handle = None
