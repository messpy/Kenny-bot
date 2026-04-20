from __future__ import annotations

import tomllib
from functools import lru_cache
from src.kennybot.utils.paths import PROMPTS_DIR


PROMPTS_PATH = PROMPTS_DIR / "prompts.toml"


@lru_cache(maxsize=1)
def _load_prompts() -> dict:
    with PROMPTS_PATH.open("rb") as fp:
        data = tomllib.load(fp)
    return data if isinstance(data, dict) else {}


def get_prompt(section: str, key: str) -> str:
    data = _load_prompts()
    value = data.get(section, {}).get(key, "")
    if not isinstance(value, str) or not value:
        raise KeyError(f"Prompt not found: {section}.{key}")
    return value
