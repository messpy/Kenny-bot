from src.kennybot.features.spam import SpamGuard, SpamPolicy


def test_everyone_cross_channel_violation_requires_two_channels_within_window() -> None:
    guard = SpamGuard(SpamPolicy(everyone_cross_channel_window_seconds=1.0))

    first = guard.record_everyone_mention(
        guild_id=1,
        user_id=10,
        channel_id=100,
        message_id=1000,
        now=10.0,
    )
    assert first is None

    same_channel = guard.record_everyone_mention(
        guild_id=1,
        user_id=10,
        channel_id=100,
        message_id=1001,
        now=10.5,
    )
    assert same_channel is None

    violation = guard.record_everyone_mention(
        guild_id=1,
        user_id=10,
        channel_id=200,
        message_id=1002,
        now=10.8,
    )

    assert violation is not None
    assert violation.guild_id == 1
    assert violation.user_id == 10
    assert {event.channel_id for event in violation.events} == {100, 200}
    assert [event.message_id for event in violation.events] == [1000, 1001, 1002]


def test_everyone_cross_channel_violation_ignores_old_messages() -> None:
    guard = SpamGuard(SpamPolicy(everyone_cross_channel_window_seconds=1.0))

    guard.record_everyone_mention(
        guild_id=1,
        user_id=10,
        channel_id=100,
        message_id=1000,
        now=10.0,
    )
    violation = guard.record_everyone_mention(
        guild_id=1,
        user_id=10,
        channel_id=200,
        message_id=1001,
        now=11.1,
    )

    assert violation is None
