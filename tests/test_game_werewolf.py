from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from src.kennybot.features.games.game_commands import (
    GameCommands,
    WerewolfRoleOptions,
    WerewolfState,
)


class WerewolfGameTests(unittest.IsolatedAsyncioTestCase):
    def _cog(self, guild: object | None = None) -> GameCommands:
        bot = SimpleNamespace(
            user=SimpleNamespace(id=999),
            get_guild=lambda _guild_id: guild,
        )
        return GameCommands(bot)

    def _state(self, **overrides: object) -> WerewolfState:
        data = {
            "guild_id": 10,
            "channel_id": 20,
            "host_user_id": 1,
            "alive_user_ids": {1, 2, 3, 4},
            "roles": {1: "人狼", 2: "占い師", 3: "騎士", 4: "村人"},
            "wolf_user_ids": {1},
            "action_message_ids": {},
            "pending_wolf_votes": {},
            "pending_guard_target": None,
            "pending_seer_target": None,
            "day_vote_message_id": None,
            "day_vote_message_ids": {},
            "day_vote_candidates": None,
            "day_vote_excluded_voter_ids": None,
            "pending_day_votes": None,
            "day_vote_runoff": False,
            "round_no": 1,
            "medium_result_target": None,
            "last_guard_target": None,
            "phase": "night",
            "resolving": False,
            "active_wolf_action_user_ids": set(),
            "active_seer_action_user_ids": set(),
            "active_knight_action_user_ids": set(),
        }
        data.update(overrides)
        return WerewolfState(**data)

    def test_rejects_multiple_seers_and_knights(self) -> None:
        cog = self._cog()

        with self.assertRaisesRegex(ValueError, "占い師は1人まで"):
            cog._build_werewolf_roles(8, WerewolfRoleOptions(seer_count=2))
        with self.assertRaisesRegex(ValueError, "騎士は1人まで"):
            cog._build_werewolf_roles(8, WerewolfRoleOptions(knight_count=2))

    def test_rejects_initial_werewolf_win_composition(self) -> None:
        cog = self._cog()

        with self.assertRaisesRegex(ValueError, "開始直後に人狼陣営の勝利条件"):
            cog._build_werewolf_roles(
                4,
                WerewolfRoleOptions(wolf_count=2, madman_count=0, seer_count=1),
            )
        with self.assertRaisesRegex(ValueError, "人狼以外の参加者"):
            cog._build_werewolf_roles(
                4,
                WerewolfRoleOptions(
                    wolf_count=4,
                    seer_count=0,
                    medium_count=0,
                    knight_count=0,
                    madman_count=0,
                ),
            )

    async def test_start_day_vote_invalidates_night_action_messages(self) -> None:
        class FakeTextChannel:
            def __init__(self) -> None:
                self.send = AsyncMock()

        class FakeMessage:
            def __init__(self, message_id: int) -> None:
                self.id = message_id
                self.add_reaction = AsyncMock()

        class FakeDM:
            def __init__(self, user_id: int) -> None:
                self.user_id = user_id

            async def send(self, _content: str) -> FakeMessage:
                return FakeMessage(1000 + self.user_id)

        def fake_member(user_id: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=user_id,
                display_name=f"user-{user_id}",
                mention=f"<@{user_id}>",
                dm_channel=FakeDM(user_id),
            )

        channel = FakeTextChannel()
        guild = SimpleNamespace(
            id=10,
            get_channel=lambda _channel_id: channel,
            get_member=fake_member,
        )
        cog = self._cog(guild)
        state = self._state(
            action_message_ids={100: ("wolf", 1)},
            active_wolf_action_user_ids={1},
            pending_wolf_votes={1: 4},
        )

        with patch("src.kennybot.features.games.game_commands.discord.TextChannel", FakeTextChannel):
            await cog._start_werewolf_day_vote(guild, state)

        self.assertEqual(state.phase, "day")
        self.assertFalse(state.resolving)
        self.assertEqual(state.action_message_ids, {})
        self.assertEqual(state.active_wolf_action_user_ids, set())

    async def test_old_night_reaction_is_ignored_during_day(self) -> None:
        guild = SimpleNamespace(get_member=lambda _user_id: None)
        cog = self._cog(guild)
        state = self._state(
            phase="day",
            action_message_ids={100: ("wolf", 1)},
            pending_wolf_votes={},
        )
        cog._werewolf_states[state.guild_id] = state
        cog._resolve_werewolf_night = AsyncMock()
        payload = SimpleNamespace(user_id=1, message_id=100, emoji="1️⃣")

        with patch("src.kennybot.features.games.game_commands.get_reaction_emojis", return_value=["1️⃣", "2️⃣"]):
            await cog.on_raw_reaction_add(payload)

        self.assertEqual(state.pending_wolf_votes, {})
        cog._resolve_werewolf_night.assert_not_awaited()

    async def test_night_resolution_uses_only_prompted_action_users(self) -> None:
        guild = SimpleNamespace(id=10)
        cog = self._cog(guild)
        state = self._state(
            active_wolf_action_user_ids=set(),
            active_seer_action_user_ids={2},
            active_knight_action_user_ids=set(),
            pending_seer_target=4,
        )
        cog._resolve_werewolf_night = AsyncMock()

        await cog._maybe_resolve_werewolf_night(guild, state)

        self.assertEqual(state.phase, "resolving_night")
        self.assertTrue(state.resolving)
        cog._resolve_werewolf_night.assert_awaited_once_with(guild, state)

    async def test_begin_round_checks_resolution_after_prompt_delivery(self) -> None:
        class FakeTextChannel:
            send = AsyncMock()

        guild = SimpleNamespace(
            id=10,
            get_channel=lambda _channel_id: FakeTextChannel(),
        )
        cog = self._cog(guild)
        state = self._state()
        cog._werewolf_states[state.guild_id] = state
        cog._send_werewolf_prompt = AsyncMock()
        cog._send_seer_prompt = AsyncMock()
        cog._send_knight_prompt = AsyncMock()
        cog._maybe_resolve_werewolf_night = AsyncMock()

        with patch("src.kennybot.features.games.game_commands.discord.TextChannel", FakeTextChannel):
            await cog._begin_werewolf_round(guild, state)

        cog._send_werewolf_prompt.assert_awaited_once_with(guild, state)
        cog._send_seer_prompt.assert_awaited_once_with(guild, state)
        cog._send_knight_prompt.assert_awaited_once_with(guild, state)
        cog._maybe_resolve_werewolf_night.assert_awaited_once_with(guild, state)

    async def test_day_vote_resolution_runs_once(self) -> None:
        guild = SimpleNamespace(id=10)
        cog = self._cog(guild)
        state = self._state(
            phase="day",
            alive_user_ids={1, 2},
            day_vote_message_ids={100: 1},
            day_vote_candidates=[1, 2],
            pending_day_votes={},
        )
        cog._werewolf_states[state.guild_id] = state
        cog._resolve_werewolf_day_vote = AsyncMock()
        payload = SimpleNamespace(user_id=1, message_id=100, emoji="2️⃣")

        with patch("src.kennybot.features.games.game_commands.get_reaction_emojis", return_value=["1️⃣", "2️⃣"]):
            await cog.on_raw_reaction_add(payload)
            await cog.on_raw_reaction_add(payload)

        self.assertEqual(state.phase, "resolving_day")
        self.assertTrue(state.resolving)
        cog._resolve_werewolf_day_vote.assert_awaited_once_with(guild, state)
        self.assertNotIn(state.guild_id, cog._werewolf_timeout_tasks)

    async def test_werewolf_night_timeout_resolves_partial_actions(self) -> None:
        class FakeTextChannel:
            def __init__(self) -> None:
                self.send = AsyncMock()

        channel = FakeTextChannel()
        guild = SimpleNamespace(
            id=10,
            get_channel=lambda _channel_id: channel,
        )
        cog = self._cog(guild)
        state = self._state(
            phase="night",
            active_wolf_action_user_ids={1},
            active_seer_action_user_ids={2},
            active_knight_action_user_ids={3},
            pending_wolf_votes={1: 4},
        )
        cog._werewolf_states[state.guild_id] = state
        cog._resolve_werewolf_night = AsyncMock()

        with patch("src.kennybot.features.games.game_commands.discord.TextChannel", FakeTextChannel):
            await cog._resolve_werewolf_timeout(guild, state, phase="night")

        self.assertEqual(state.phase, "resolving_night")
        self.assertTrue(state.resolving)
        cog._resolve_werewolf_night.assert_awaited_once_with(guild, state)
        channel.send.assert_awaited_once()

    async def test_werewolf_day_timeout_resolves_partial_votes(self) -> None:
        class FakeTextChannel:
            def __init__(self) -> None:
                self.send = AsyncMock()

        channel = FakeTextChannel()
        guild = SimpleNamespace(
            id=10,
            get_channel=lambda _channel_id: channel,
        )
        cog = self._cog(guild)
        state = self._state(
            phase="day",
            day_vote_candidates=[1, 2, 3],
            pending_day_votes={1: 2},
        )
        cog._werewolf_states[state.guild_id] = state
        cog._resolve_werewolf_day_vote = AsyncMock()

        with patch("src.kennybot.features.games.game_commands.discord.TextChannel", FakeTextChannel):
            await cog._resolve_werewolf_timeout(guild, state, phase="day")

        self.assertEqual(state.phase, "resolving_day")
        self.assertTrue(state.resolving)
        cog._resolve_werewolf_day_vote.assert_awaited_once_with(guild, state)
        channel.send.assert_awaited_once()

    async def test_wait_for_game_start_times_out(self) -> None:
        import asyncio

        async def never_returns(*_args: object, **_kwargs: object) -> None:
            return None

        async def fake_wait_for(coro: object, *, timeout: int) -> None:
            del timeout
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            raise asyncio.TimeoutError

        cog = GameCommands(SimpleNamespace(user=SimpleNamespace(id=999), wait_for=never_returns))
        with patch("src.kennybot.features.games.game_commands.asyncio.wait_for", side_effect=fake_wait_for):
            self.assertFalse(await cog._wait_for_game_start(10, 1, timeout_sec=30))


if __name__ == "__main__":
    unittest.main()
