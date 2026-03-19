from __future__ import annotations

import json
from pathlib import Path


BUILD_INFO_PATH = Path("data") / "build_info.json"


def load_build_info() -> dict[str, str]:
    try:
        raw = json.loads(BUILD_INFO_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
    except Exception:
        pass
    return {}
