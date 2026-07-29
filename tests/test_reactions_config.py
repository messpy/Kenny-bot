from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from src.kennybot.utils.reactions import (
    get_keyword_reactions,
    get_reaction_emoji,
    get_reaction_emojis,
    reaction_aliases,
)


class ReactionConfigTests(TestCase):
    def test_reaction_emoji_uses_configured_value(self) -> None:
        settings = SimpleNamespace(get=lambda path, default=None, guild_id=None: "🧪" if path == "reactions.ai_review" else default)

        with patch("src.kennybot.utils.reactions.get_settings", return_value=settings):
            self.assertEqual(get_reaction_emoji("ai_review"), "🧪")

    def test_reaction_emoji_list_uses_configured_values(self) -> None:
        settings = SimpleNamespace(
            get=lambda path, default=None, guild_id=None: ["A", "B", "A", ""] if path == "reactions.werewolf.votes" else default
        )

        with patch("src.kennybot.utils.reactions.get_settings", return_value=settings):
            self.assertEqual(get_reaction_emojis("werewolf.votes"), ["A", "B"])

    def test_reaction_aliases_drop_variation_selector(self) -> None:
        self.assertEqual(reaction_aliases("▶️"), {"▶️", "▶"})

    def test_keyword_reactions_prefers_new_reactions_section(self) -> None:
        def fake_get(path: str, default=None, guild_id=None):
            if path == "reactions.keyword":
                return {"ミュ": "🐈"}
            if path == "keyword_reactions":
                return {"ミュ": "old", "いいね": "👍"}
            return default

        with patch("src.kennybot.utils.reactions.get_settings", return_value=SimpleNamespace(get=fake_get)):
            self.assertEqual(get_keyword_reactions(), {"ミュ": "🐈", "いいね": "👍"})

    def test_keyword_reactions_keeps_legacy_fallback(self) -> None:
        def fake_get(path: str, default=None, guild_id=None):
            if path == "reactions.keyword":
                return None
            if path == "keyword_reactions":
                return {"いいね": "👍"}
            return default

        with patch("src.kennybot.utils.reactions.get_settings", return_value=SimpleNamespace(get=fake_get)):
            self.assertEqual(get_keyword_reactions(), {"いいね": "👍"})
