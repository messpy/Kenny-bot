import unittest

from src.kennybot.features.spam import SpamGuard, SpamPolicy


class SpamGuardTest(unittest.TestCase):
    def test_everyone_mention_requires_three_posts_within_two_seconds(self) -> None:
        guard = SpamGuard(
            SpamPolicy(
                everyone_mention_window_seconds=2.0,
                everyone_mention_trigger_count=3,
            )
        )

        first = guard.record_everyone_mention(
            guild_id=1,
            user_id=10,
            channel_id=100,
            message_id=1000,
            now=10.0,
        )
        self.assertIsNone(first)

        second = guard.record_everyone_mention(
            guild_id=1,
            user_id=10,
            channel_id=100,
            message_id=1001,
            now=11.0,
        )
        self.assertIsNone(second)

        violation = guard.record_everyone_mention(
            guild_id=1,
            user_id=10,
            channel_id=100,
            message_id=1002,
            now=11.8,
        )

        self.assertIsNotNone(violation)
        self.assertEqual(violation.guild_id, 1)
        self.assertEqual(violation.user_id, 10)
        self.assertEqual([event.message_id for event in violation.events], [1000, 1001, 1002])

    def test_everyone_mention_accepts_everyone_and_here_in_same_window(self) -> None:
        guard = SpamGuard(
            SpamPolicy(
                everyone_mention_window_seconds=2.0,
                everyone_mention_trigger_count=3,
            )
        )

        guard.record_everyone_mention(
            guild_id=1,
            user_id=10,
            channel_id=100,
            message_id=1000,
            now=10.0,
        )
        guard.record_everyone_mention(
            guild_id=1,
            user_id=10,
            channel_id=100,
            message_id=1001,
            now=10.5,
        )
        violation = guard.record_everyone_mention(
            guild_id=1,
            user_id=10,
            channel_id=100,
            message_id=1002,
            now=11.0,
        )

        self.assertIsNotNone(violation)
        self.assertEqual([event.message_id for event in violation.events], [1000, 1001, 1002])

    def test_everyone_mention_ignores_old_messages(self) -> None:
        guard = SpamGuard(
            SpamPolicy(
                everyone_mention_window_seconds=2.0,
                everyone_mention_trigger_count=3,
            )
        )

        guard.record_everyone_mention(
            guild_id=1,
            user_id=10,
            channel_id=100,
            message_id=1000,
            now=10.0,
        )
        guard.record_everyone_mention(
            guild_id=1,
            user_id=10,
            channel_id=100,
            message_id=1001,
            now=12.1,
        )
        violation = guard.record_everyone_mention(
            guild_id=1,
            user_id=10,
            channel_id=100,
            message_id=1002,
            now=12.2,
        )

        self.assertIsNone(violation)


if __name__ == "__main__":
    unittest.main()
