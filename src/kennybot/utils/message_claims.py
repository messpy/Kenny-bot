import os
import time
from pathlib import Path


class MessageClaimStore:
    """File-backed, cross-process claim guard for Discord message IDs."""

    def __init__(self, claim_dir: Path, *, prune_interval_seconds: int = 300) -> None:
        self.claim_dir = claim_dir
        self.prune_interval_seconds = prune_interval_seconds
        self._last_prune = 0.0

    def prune(self, *, max_age_seconds: int = 86400) -> None:
        now = time.time()
        if now - self._last_prune < self.prune_interval_seconds:
            return
        self._last_prune = now
        try:
            self.claim_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

        for path in self.claim_dir.glob("*.claim"):
            try:
                if now - path.stat().st_mtime > max_age_seconds:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def claim_once(self, message_id: int) -> bool:
        if int(message_id or 0) <= 0:
            return True

        self.prune()
        try:
            self.claim_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return True

        claim_path = self.claim_dir / f"{message_id}.claim"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(claim_path, flags, 0o644)
        except FileExistsError:
            return False
        except OSError:
            return True

        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{time.time()}\n")
        return True
