import unittest

from src.kennybot.utils.text import sanitize_user_visible_error


class TextSanitizerTests(unittest.TestCase):
    def test_sanitize_user_visible_error_redacts_paths(self) -> None:
        text = "failed reading /home/kennypi/work/Kenny-bot/src/kennybot/bot.py and C:\\Users\\kenny\\bot\\secret.py"

        sanitized = sanitize_user_visible_error(text)

        self.assertIn("[path]", sanitized)
        self.assertNotIn("/home/kennypi", sanitized)
        self.assertNotIn("C:\\Users", sanitized)

    def test_sanitize_user_visible_error_removes_traceback_source_context(self) -> None:
        text = "\n".join(
            [
                "Traceback (most recent call last):",
                '  File "/home/kennypi/work/Kenny-bot/src/kennybot/bot.py", line 42, in on_message',
                "    secret = open('/home/kennypi/token').read()",
                "RuntimeError: failed in /home/kennypi/work/Kenny-bot/src/kennybot/bot.py",
            ]
        )

        sanitized = sanitize_user_visible_error(text)

        self.assertNotIn("File ", sanitized)
        self.assertNotIn("secret = open", sanitized)
        self.assertNotIn("/home/kennypi", sanitized)
        self.assertIn("RuntimeError", sanitized)

    def test_sanitize_user_visible_error_has_generic_fallback(self) -> None:
        self.assertEqual(
            sanitize_user_visible_error(""),
            "詳細はログを確認してください。",
        )


if __name__ == "__main__":
    unittest.main()
