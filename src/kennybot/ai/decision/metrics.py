from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock

from src.kennybot.ai.decision.models import ShadowMetrics


logger = logging.getLogger(__name__)


class DecisionMetricsStore:
    """Private JSONL metrics store; never writes Discord identifiers or message text."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def append(self, metrics: ShadowMetrics) -> None:
        payload = metrics.as_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")

    def safe_append(self, metrics: ShadowMetrics) -> None:
        try:
            self.append(metrics)
        except Exception:
            logger.exception("Failed to write decision shadow metrics")
