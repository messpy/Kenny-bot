from __future__ import annotations

from typing import Any

from src.kennybot.utils.runtime_settings import get_settings


DEFAULT_REACTION_EMOJIS: dict[str, str] = {
    "ai_review": "🤔",
    "weekly_today_language.unknown": "✋",
    "weekly_today_language.known": "👀",
    "weekly_today_language.learned": "✅",
    "weekly_today_language.issue": "⚠️",
    "mod_reset": "🔄",
    "mod_list": "📋",
    "vc.join": "✅",
    "vc.mute_on": "🔇",
    "vc.mute_off": "🎤",
    "vc.deaf_on": "🙉",
    "vc.deaf_off": "🙊",
    "group_match.join": "🤝",
    "group_match.start": "▶️",
    "minutes.summary": "⏯️",
    "minutes.stop": "⏹️",
    "minutes.playback": "🎶",
    "minutes.realtime": "▶️",
    "timer.restart": "🔁",
    "game.join": "🎮",
    "game.start": "▶️",
    "wordwolf.end": "⏹️",
    "wordwolf.repeat": "🔁",
}

DEFAULT_REACTION_EMOJI_LISTS: dict[str, tuple[str, ...]] = {
    "werewolf.votes": ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"),
}


def _clean_emoji(value: Any) -> str:
    return str(value or "").strip()


def get_reaction_emoji(key: str, *, guild_id: int | None = None) -> str:
    fallback = DEFAULT_REACTION_EMOJIS.get(key, "")
    value = get_settings().get(f"reactions.{key}", fallback, guild_id=guild_id)
    return _clean_emoji(value) or fallback


def get_reaction_emojis(key: str, *, guild_id: int | None = None) -> list[str]:
    fallback = list(DEFAULT_REACTION_EMOJI_LISTS.get(key, ()))
    value = get_settings().get(f"reactions.{key}", fallback, guild_id=guild_id)
    values = value if isinstance(value, (list, tuple, set)) else fallback
    emojis: list[str] = []
    for item in values:
        emoji = _clean_emoji(item)
        if emoji and emoji not in emojis:
            emojis.append(emoji)
    return emojis or fallback


def reaction_aliases(emoji: str) -> set[str]:
    cleaned = _clean_emoji(emoji)
    if not cleaned:
        return set()
    aliases = {cleaned}
    without_variation = cleaned.replace("\ufe0f", "")
    if without_variation:
        aliases.add(without_variation)
    return aliases


def get_keyword_reactions(*, guild_id: int | None = None) -> dict[str, str]:
    settings = get_settings()
    reactions: dict[str, str] = {}
    for configured in (
        settings.get("keyword_reactions", {}, guild_id=guild_id),
        settings.get("reactions.keyword", {}, guild_id=guild_id),
    ):
        if not isinstance(configured, dict):
            continue
        for keyword, emoji in configured.items():
            key = str(keyword or "").strip()
            value = _clean_emoji(emoji)
            if key and value:
                reactions[key] = value
    return reactions
