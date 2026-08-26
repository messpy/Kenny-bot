import unittest

from src.kennybot.utils.discord_markdown import DiscordMarkdown


class DiscordMarkdownTests(unittest.TestCase):
    def test_link_uses_discord_angle_bracket_url_syntax(self) -> None:
        self.assertEqual(
            DiscordMarkdown.link("メッセージ", "https://example.com/a?x=1"),
            "[メッセージ](<https://example.com/a?x=1>)",
        )

    def test_link_escapes_angle_brackets_in_url(self) -> None:
        self.assertEqual(
            DiscordMarkdown.link("位置図", "https://example.com/a<bad>"),
            "[位置図](<https://example.com/a%3Cbad%3E>)",
        )


if __name__ == "__main__":
    unittest.main()
