from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from src.kennybot.features.games.game_commands import AvalonState, GameCommands


class AvalonGameTests(unittest.IsolatedAsyncioTestCase):
    def _cog(self, guild: object | None = None) -> GameCommands:
        bot = SimpleNamespace(
            user=SimpleNamespace(id=999),
            get_guild=lambda _guild_id: guild,
        )
        return GameCommands(bot)

    def _state(self, **overrides: object) -> AvalonState:
        participant_ids = [1, 2, 3, 4, 5]
        roles = {
            1: "Merlin",
            2: "Loyal Servant",
            3: "Loyal Servant",
            4: "Assassin",
            5: "Minion",
        }
        data = {
            "guild_id": 10,
            "channel_id": 20,
            "host_user_id": 1,
            "participant_user_ids": participant_ids,
            "roles": roles,
            "good_user_ids": {1, 2, 3},
            "evil_user_ids": {4, 5},
            "leader_index": 0,
            "quest_no": 1,
            "successes": 0,
            "failures": 0,
            "proposal_no": 1,
            "phase": "team_select",
            "team_size": 2,
            "team_selection_message_id": 100,
            "selected_team_user_ids": set(),
            "vote_message_ids": {},
            "pending_votes": {},
            "quest_message_ids": {},
            "pending_quest_cards": {},
            "assassin_message_id": None,
            "assassin_user_id": None,
        }
        data.update(overrides)
        return AvalonState(**data)

    def test_avalon_quest_team_sizes_are_fixed_by_player_count(self) -> None:
        cog = self._cog()

        self.assertEqual(
            [cog._avalon_team_size(5, quest_no) for quest_no in range(1, 6)],
            [2, 3, 2, 3, 3],
        )
        self.assertEqual(
            [cog._avalon_team_size(10, quest_no) for quest_no in range(1, 6)],
            [3, 4, 4, 5, 5],
        )
        self.assertEqual(cog._avalon_fail_threshold(7, 4), 2)
        self.assertEqual(cog._avalon_fail_threshold(6, 4), 1)

    def test_build_avalon_roles_has_required_sides(self) -> None:
        cog = self._cog()

        roles = cog._build_avalon_roles([1, 2, 3, 4, 5, 6, 7])
        role_values = list(roles.values())

        self.assertEqual(len(roles), 7)
        self.assertEqual(role_values.count("Merlin"), 1)
        self.assertEqual(role_values.count("Assassin"), 1)
        self.assertEqual(
            sum(1 for role in role_values if role in {"Assassin", "Minion"}),
            3,
        )

    async def test_team_vote_requires_majority_of_all_players(self) -> None:
        class FakeTextChannel:
            def __init__(self) -> None:
                self.send = AsyncMock()

        channel = FakeTextChannel()
        guild = SimpleNamespace(id=10, get_channel=lambda _channel_id: channel)
        cog = self._cog(guild)
        state = self._state(
            phase="team_vote",
            vote_message_ids={100: 1, 101: 2, 102: 3},
            pending_votes={1: True, 2: True, 3: False},
        )
        cog._start_avalon_quest = AsyncMock()
        cog._reject_avalon_team = AsyncMock()

        with patch("src.kennybot.features.games.game_commands.discord.TextChannel", FakeTextChannel):
            await cog._resolve_avalon_team_vote(guild, state)

        cog._start_avalon_quest.assert_not_awaited()
        cog._reject_avalon_team.assert_awaited_once_with(guild, state)

        state.pending_votes[3] = True
        await cog._resolve_avalon_team_vote(guild, state)
        cog._start_avalon_quest.assert_awaited_once_with(guild, state)

    async def test_quest_four_with_seven_players_needs_two_fail_cards(self) -> None:
        class FakeTextChannel:
            def __init__(self) -> None:
                self.send = AsyncMock()

        channel = FakeTextChannel()
        guild = SimpleNamespace(id=10, get_channel=lambda _channel_id: channel)
        cog = self._cog(guild)
        participant_ids = [1, 2, 3, 4, 5, 6, 7]
        state = self._state(
            participant_user_ids=participant_ids,
            quest_no=4,
            selected_team_user_ids={1, 4, 5, 6},
            pending_quest_cards={1: True, 4: False, 5: True, 6: True},
        )
        cog._begin_avalon_proposal = AsyncMock()

        with patch("src.kennybot.features.games.game_commands.discord.TextChannel", FakeTextChannel):
            await cog._resolve_avalon_quest(guild, state)

        self.assertEqual(state.successes, 1)
        self.assertEqual(state.failures, 0)

        state.quest_no = 4
        state.successes = 0
        state.pending_quest_cards = {1: True, 4: False, 5: False, 6: True}
        await cog._resolve_avalon_quest(guild, state)

        self.assertEqual(state.failures, 1)

    async def test_assassin_wins_only_when_targeting_merlin(self) -> None:
        guild = SimpleNamespace(id=10)
        cog = self._cog(guild)
        state = self._state(
            phase="assassination",
            assassin_message_id=100,
            assassin_user_id=4,
        )
        cog._avalon_states[state.guild_id] = state
        cog._announce_avalon_end = AsyncMock()

        payload = SimpleNamespace(user_id=4, message_id=100, emoji="1️⃣")

        with patch("src.kennybot.features.games.game_commands.get_reaction_emojis", return_value=["1️⃣", "2️⃣", "3️⃣"]):
            await cog.on_raw_reaction_add(payload)

        args = cog._announce_avalon_end.await_args.args
        self.assertIn("邪悪陣営の勝ち", args[2])

    async def test_only_leader_can_select_quest_team(self) -> None:
        guild = SimpleNamespace(id=10)
        cog = self._cog(guild)
        state = self._state(
            phase="team_select",
            team_selection_message_id=100,
            selected_team_user_ids=set(),
        )
        cog._avalon_states[state.guild_id] = state
        cog._start_avalon_team_vote = AsyncMock()

        with patch("src.kennybot.features.games.game_commands.get_reaction_emojis", return_value=["1️⃣", "2️⃣", "3️⃣"]):
            await cog.on_raw_reaction_add(SimpleNamespace(user_id=2, message_id=100, emoji="1️⃣"))

        self.assertEqual(state.selected_team_user_ids, set())
        cog._start_avalon_team_vote.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
