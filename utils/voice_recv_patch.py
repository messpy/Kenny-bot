from __future__ import annotations

import logging


logger = logging.getLogger(__name__)
_PATCHED = False


def apply_voice_recv_resilience_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True
    logger.info("voice_recv resilience patch disabled")
