# cogs/game_commands.py
# ミニゲーム用スラッシュコマンド

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.kennybot.utils.command_catalog import get_slash_command_meta
from src.kennybot.utils.runtime_settings import get_settings
from src.kennybot.utils.text import strip_ansi_and_ctrl
from src.kennybot.utils.prompts import get_prompt

_settings = get_settings()
GAME_META = get_slash_command_meta("game")


@dataclass
class DMResult:
    user: discord.Member
    success: bool
    reason: str = ""


@dataclass
class AiueoBattleState:
    guild_id: int
    channel_id: int
    host_user_id: int
    turn_user_ids: list[int]
    active_user_ids: set[int]
    secret_words: dict[int, str]
    used_chars: set[str]
    revealed_chars: dict[int, set[str]]
    turn_index: int = 0
    attacks_used_in_turn: int = 0


@dataclass
class WerewolfState:
    guild_id: int
    channel_id: int
    host_user_id: int
    alive_user_ids: set[int]
    roles: dict[int, str]
    wolf_user_ids: set[int]
    action_message_ids: dict[int, tuple[str, int]]
    pending_wolf_votes: dict[int, int]
    pending_guard_target: int | None = None
    pending_seer_target: int | None = None
    day_vote_message_id: int | None = None
    day_vote_message_ids: dict[int, int] | None = None
    day_vote_candidates: list[int] | None = None
    day_vote_excluded_voter_ids: set[int] | None = None
    pending_day_votes: dict[int, int] | None = None
    day_vote_runoff: bool = False
    round_no: int = 1
    medium_result_target: int | None = None
    last_guard_target: int | None = None


@dataclass
class WordWolfSessionState:
    guild_id: int
    channel_id: int
    host_user_id: int
    participant_user_ids: list[int]
    category: str | None
    minority_count: int
    debug_enabled: bool
    round_no: int = 0
    common_word: str = ""
    odd_word: str = ""
    minority_user_ids: set[int] | None = None
    active: bool = False


@dataclass
class GameLobbyState:
    guild_id: int
    channel_id: int
    host_user_id: int
    mode_name: str


@dataclass(frozen=True)
class WerewolfRoleOptions:
    wolf_count: int | None = None
    seer_count: int | None = None
    medium_count: int | None = None
    knight_count: int | None = None
    madman_count: int | None = None


class GameCommands(commands.Cog):
    """ミニゲームコマンド"""

    JOIN_EMOJI = "🎮"
    START_EMOJI = "▶️"
    WORDWOLF_END_EMOJI = "⏹️"
    WORDWOLF_REPEAT_EMOJI = "🔁"
    WORDWOLF_PAIRS_PATH = Path("data") / "wordwolf_pairs.json"
    WEREWOLF_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    WEREWOLF_DEBUG_NAMES = ["田中", "佐藤", "鈴木"]
    AIUEO_ROWS = [
        "あいうえお",
        "かきくけこ",
        "さしすせそ",
        "たちつてと",
        "なにぬねの",
        "はひふへほ",
        "まみむめも",
        "やゆよ",
        "らりるれろ",
        "わをん",
    ]
    AIUEO_ALLOWED = set("".join(AIUEO_ROWS))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._aiueo_states: dict[int, AiueoBattleState] = {}
        self._werewolf_states: dict[int, WerewolfState] = {}
        self._wordwolf_sessions: dict[int, WordWolfSessionState] = {}
        self._game_lobbies: dict[int, GameLobbyState] = {}
        self._recent_wordwolf_pairs: list[tuple[str, str]] = []
        self._saved_wordwolf_pairs: list[tuple[str, str]] = self._load_saved_wordwolf_pairs()

    @app_commands.command(name=GAME_META.name, description=GAME_META.description)
    @app_commands.checks.cooldown(1, 20.0)
    @app_commands.describe(
        mode="ゲーム種類",
        action="操作（あいうえおバトル時: 開始/文字宣言/状況/終了）",
        char="あいうえおバトルで宣言する1文字（action=文字宣言）",
        category="任意カテゴリ（例: 動物, 食べ物, 映画）",
        minority_count="ワードウルフの少数派人数（1以上）",
        debug="デバッグモードで開始するか",
        wolf_count="人狼人数（人狼役職配布のみ）",
        seer_count="占い師人数（人狼役職配布のみ）",
        medium_count="霊媒師人数（人狼役職配布のみ）",
        knight_count="騎士人数（人狼役職配布のみ）",
        madman_count="狂人人数（人狼役職配布のみ）",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="配布: 数字", value="number"),
            app_commands.Choice(name="配布: 単語", value="words"),
            app_commands.Choice(name="ワードウルフ", value="wordwolf"),
            app_commands.Choice(name="人狼役職配布", value="werewolf"),
            app_commands.Choice(name="あいうえおバトル", value="aiueo_battle"),
        ],
        action=[
            app_commands.Choice(name="開始", value="start"),
            app_commands.Choice(name="文字宣言", value="char"),
            app_commands.Choice(name="状況表示", value="status"),
            app_commands.Choice(name="終了", value="end"),
        ],
        debug=[
            app_commands.Choice(name="OFF", value=0),
            app_commands.Choice(name="ON", value=1),
        ],
    )
    async def game(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
        action: app_commands.Choice[str] | None = None,
        char: str | None = None,
        category: str | None = None,
        minority_count: app_commands.Range[int, 1, 9] | None = None,
        debug: app_commands.Choice[int] | None = None,
        wolf_count: app_commands.Range[int, 0, 9] | None = None,
        seer_count: app_commands.Range[int, 0, 9] | None = None,
        medium_count: app_commands.Range[int, 0, 9] | None = None,
        knight_count: app_commands.Range[int, 0, 9] | None = None,
        madman_count: app_commands.Range[int, 0, 9] | None = None,
    ):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("サーバーのテキストチャンネルで実行してください。", ephemeral=True)
            return

        action_value = action.value if action else "start"
        debug_enabled = bool(debug.value) if debug is not None else False
        werewolf_role_options = WerewolfRoleOptions(
            wolf_count=int(wolf_count) if wolf_count is not None else None,
            seer_count=int(seer_count) if seer_count is not None else None,
            medium_count=int(medium_count) if medium_count is not None else None,
            knight_count=int(knight_count) if knight_count is not None else None,
            madman_count=int(madman_count) if madman_count is not None else None,
        )

        if mode.value == "aiueo_battle":
            if action_value == "char":
                await self._aiueo_char_action(interaction, char or "")
                return
            if action_value == "status":
                await self._aiueo_status_action(interaction)
                return
            if action_value == "end":
                await self._aiueo_end_action(interaction)
                return
        elif action_value != "start":
            await interaction.response.send_message(
                "この操作は `mode=あいうえおバトル` のときだけ使えます。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        recruit = await interaction.channel.send(
            self._build_game_lobby_content(mode.name, interaction.user.id, [])
        )
        await recruit.add_reaction(self.JOIN_EMOJI)
        await recruit.add_reaction(self.START_EMOJI)
        self._game_lobbies[recruit.id] = GameLobbyState(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            host_user_id=interaction.user.id,
            mode_name=mode.name,
        )
        await interaction.followup.send(
            f"募集メッセージを作成しました。参加者が揃ったら、そのメッセージに {self.START_EMOJI} を押してください。",
            ephemeral=True,
        )
        await self._wait_for_game_start(recruit.id, interaction.user.id)
        self._game_lobbies.pop(recruit.id, None)
        await recruit.edit(
            content=(
                f"🎲 **{mode.name}** 参加受付を終了しました。\n"
                f"{self.JOIN_EMOJI} で参加したユーザーを集計中..."
            )
        )

        recruit = await interaction.channel.fetch_message(recruit.id)
        participants = await self._collect_participants(recruit, interaction.guild)

        min_players = 1 if mode.value in ("number", "words") else 2
        if mode.value == "wordwolf":
            requested_minority = int(minority_count or 1)
            min_players = 1 if debug_enabled else max(3, requested_minority + 2)
        elif mode.value == "werewolf":
            min_players = 1 if debug_enabled else self._minimum_werewolf_players(werewolf_role_options)
        if len(participants) < min_players:
            await interaction.followup.send(
                f"参加者が不足しています。`{mode.name}` は最低 {min_players} 人必要です。",
                ephemeral=True,
            )
            return

        if mode.value == "aiueo_battle":
            await self._start_aiueo_battle(interaction, participants)
            return

        if mode.value == "number":
            results = await self._run_number(participants)
            title = "ランダム数字(0-100)をDM送信しました"
        elif mode.value == "words":
            results = await self._run_words(participants, category)
            title = "単語をDM送信しました"
        elif mode.value == "wordwolf":
            requested_minority = int(minority_count or 1)
            if not debug_enabled and requested_minority >= len(participants) - 1:
                await interaction.followup.send(
                    "少数派人数は、参加者数に対して多すぎます。多数派を2人以上残してください。",
                    ephemeral=True,
                )
                return
            results = await self._start_wordwolf_session(
                interaction=interaction,
                members=participants,
                category=category,
                minority_count=requested_minority,
                debug_enabled=debug_enabled,
            )
            title = "ワードウルフ配布をDM送信しました"
        else:
            if debug_enabled:
                await self._run_werewolf_debug(interaction, werewolf_role_options)
                return
            results = await self._run_werewolf(
                interaction,
                participants,
                debug_enabled=debug_enabled,
                role_options=werewolf_role_options,
            )
            title = "人狼役職をDM送信しました"

        ok = [r.user.mention for r in results if r.success]
        ng = [f"{r.user.mention} ({r.reason})" for r in results if not r.success]

        embed = discord.Embed(title=f"🎮 {title}", color=discord.Color.blue())
        embed.add_field(name="参加者数", value=str(len(participants)), inline=True)
        embed.add_field(name="DM成功", value=str(len(ok)), inline=True)
        embed.add_field(name="DM失敗", value=str(len(ng)), inline=True)
        if ok:
            embed.add_field(name="成功ユーザー", value=", ".join(ok)[:1000], inline=False)
        if ng:
            embed.add_field(name="失敗ユーザー", value="\n".join(ng)[:1000], inline=False)

        await interaction.followup.send(
            content=f"{interaction.user.mention} ゲーム配布を実行しました（この結果はあなたにのみ表示）。",
            embed=embed,
            ephemeral=True,
        )

    async def _run_werewolf_debug(
        self,
        interaction: discord.Interaction,
        role_options: WerewolfRoleOptions,
    ) -> None:
        assert interaction.channel and isinstance(interaction.channel, discord.TextChannel)
        names = list(self.WEREWOLF_DEBUG_NAMES)
        try:
            roles = self._build_werewolf_roles(len(names), role_options)
        except ValueError as exc:
            await interaction.followup.send(f"人狼デバッグモードを開始できません: {exc}", ephemeral=True)
            return
        random.shuffle(roles)
        lines = [
            "🐺 **人狼デバッグモード**",
            "架空の3人で役職配布結果を表示します。",
            "",
            "参加者:",
        ]
        lines.extend(f"- {name}" for name in names)
        lines.extend(
            [
                "",
                "役職:",
            ]
        )
        lines.extend(f"- {name}: **{role}**" for name, role in zip(names, roles))
        await interaction.channel.send("\n".join(lines))
        await interaction.followup.send(
            "人狼デバッグモードを実行しました。架空の3人をチャンネルに表示しました。",
            ephemeral=True,
        )

    async def _wait_for_game_start(self, message_id: int, host_user_id: int) -> None:
        def check(payload: discord.RawReactionActionEvent) -> bool:
            return (
                payload.message_id == message_id
                and payload.user_id == host_user_id
                and str(payload.emoji) == self.START_EMOJI
            )

        await self.bot.wait_for("raw_reaction_add", check=check)

    def _build_game_lobby_content(
        self,
        mode_name: str,
        host_user_id: int,
        participants: list[discord.Member],
    ) -> str:
        lines = [
            f"🎲 **{mode_name}** を開始します。",
            f"{self.JOIN_EMOJI} を押した人が参加者です。",
            f"<@{host_user_id}> が {self.START_EMOJI} を押すと開始します。",
            f"現在の参加者: {len(participants)}人",
            "",
        ]
        if not participants:
            lines.append("まだ参加者はいません。")
            return "\n".join(lines)
        lines.append("参加者:")
        lines.extend(f"- {member.mention}" for member in participants)
        return "\n".join(lines)

    async def _refresh_game_lobby(self, message_id: int) -> None:
        lobby = self._game_lobbies.get(message_id)
        if lobby is None:
            return
        guild = self.bot.get_guild(lobby.guild_id)
        channel = self.bot.get_channel(lobby.channel_id)
        if guild is None or not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(message_id)
        except Exception:
            self._game_lobbies.pop(message_id, None)
            return
        participants = await self._collect_participants(message, guild)
        content = self._build_game_lobby_content(lobby.mode_name, lobby.host_user_id, participants)
        if message.content != content:
            await message.edit(content=content)

    async def _aiueo_char_action(self, interaction: discord.Interaction, char: str):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("サーバーのテキストチャンネルで実行してください。", ephemeral=True)
            return

        state = self._aiueo_states.get(interaction.channel.id)
        if not state or state.guild_id != interaction.guild.id:
            await interaction.response.send_message("このチャンネルで進行中のあいうえおバトルはありません。", ephemeral=True)
            return
        if interaction.user.id not in state.active_user_ids:
            await interaction.response.send_message("あなたはこのゲームの参加者ではないか、すでに脱落しています。", ephemeral=True)
            return

        cur_uid = self._current_turn_user_id(state)
        if interaction.user.id != cur_uid:
            await interaction.response.send_message("あなたのターンではありません。", ephemeral=True)
            return

        ch = (char or "").strip()
        error = self._validate_aiueo_attack_char(state, ch)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        text, ended = self._apply_aiueo_attack(interaction.guild, interaction.channel.id, state, interaction.user.id, ch)
        await interaction.response.send_message(text)
        if ended:
            self._aiueo_states.pop(interaction.channel.id, None)

    async def _aiueo_status_action(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("サーバーのテキストチャンネルで実行してください。", ephemeral=True)
            return
        state = self._aiueo_states.get(interaction.channel.id)
        if not state or state.guild_id != interaction.guild.id:
            await interaction.response.send_message("このチャンネルで進行中のあいうえおバトルはありません。", ephemeral=True)
            return
        cur_uid = self._current_turn_user_id(state)
        board = self._render_aiueo_board(state.used_chars)
        status = self._render_aiueo_status(interaction.guild, state)
        await interaction.response.send_message(
            f"現在のターン: <@{cur_uid}>\n\n{board}\n\n{status}",
            ephemeral=True,
        )

    async def _aiueo_end_action(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("サーバーのテキストチャンネルで実行してください。", ephemeral=True)
            return
        state = self._aiueo_states.get(interaction.channel.id)
        if not state or state.guild_id != interaction.guild.id:
            await interaction.response.send_message("このチャンネルで進行中のあいうえおバトルはありません。", ephemeral=True)
            return

        is_host = interaction.user.id == state.host_user_id
        is_admin = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild
        if not (is_host or is_admin):
            await interaction.response.send_message("終了できるのはホストか管理者のみです。", ephemeral=True)
            return
        self._aiueo_states.pop(interaction.channel.id, None)
        await interaction.response.send_message("🛑 あいうえおバトルを終了しました。")

    async def _collect_participants(self, message: discord.Message, guild: discord.Guild) -> list[discord.Member]:
        users: list[discord.Member] = []
        seen: set[int] = set()
        for reaction in message.reactions:
            if str(reaction.emoji) != self.JOIN_EMOJI:
                continue
            async for u in reaction.users():
                if u.bot or u.id in seen:
                    continue
                m = guild.get_member(u.id)
                if isinstance(m, discord.Member):
                    users.append(m)
                    seen.add(u.id)
        return users

    async def _run_number(self, members: list[discord.Member]) -> list[DMResult]:
        out: list[DMResult] = []
        for m in members:
            n = random.randint(0, 100)
            out.append(await self._safe_dm(m, f"🎲 あなたの数字は **{n}** です。"))
        return out

    async def _run_words(self, members: list[discord.Member], category: str | None) -> list[DMResult]:
        words = await self._generate_words(len(members), category)
        out: list[DMResult] = []
        for m, w in zip(members, words):
            out.append(await self._safe_dm(m, f"📝 あなたの単語: **{w}**"))
        return out

    def _build_wordwolf_control_text(
        self,
        session: WordWolfSessionState,
        guild: discord.Guild,
    ) -> str:
        names = []
        for uid in session.participant_user_ids:
            member = guild.get_member(uid)
            names.append(member.mention if member else f"<@{uid}>")
        lines = [
            f"🐺 **ワードウルフ Round {session.round_no}**",
            f"参加者: {len(session.participant_user_ids)}人",
            " / ".join(names),
            "",
            f"{self.WORDWOLF_END_EMOJI} を主催者が押すと結果公開",
        ]
        if not session.active:
            lines.append(f"{self.WORDWOLF_REPEAT_EMOJI} を主催者が押すと同じメンバーで再配布")
        return "\n".join(lines)

    def _build_wordwolf_result_text(
        self,
        session: WordWolfSessionState,
        guild: discord.Guild,
    ) -> str:
        minority_ids = session.minority_user_ids or set()
        lines = [
            f"🏁 **ワードウルフ Round {session.round_no} 結果**",
            f"多数派単語: **{session.common_word or '不明'}**",
            f"少数派単語: **{session.odd_word or '不明'}**",
            "",
            "役割公開:",
        ]
        for uid in session.participant_user_ids:
            member = guild.get_member(uid)
            mention = member.mention if member else f"<@{uid}>"
            role = "ワードウルフ" if uid in minority_ids else "多数派"
            lines.append(f"- {mention}: {role}")
        lines.extend(
            [
                "",
                f"{self.WORDWOLF_REPEAT_EMOJI} を主催者が押すと同じメンバーで再配布",
            ]
        )
        return "\n".join(lines)

    def _load_saved_wordwolf_pairs(self) -> list[tuple[str, str]]:
        path = self.WORDWOLF_PAIRS_PATH
        try:
            if not path.exists():
                return []
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            pairs: list[tuple[str, str]] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                common = str(item.get("common", "")).strip()
                odd = str(item.get("odd", "")).strip()
                if common and odd and common != odd:
                    pairs.append((common, odd))
            return pairs[-200:]
        except Exception:
            return []

    def _save_wordwolf_pairs(self) -> None:
        path = self.WORDWOLF_PAIRS_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = [{"common": common, "odd": odd} for common, odd in self._saved_wordwolf_pairs[-200:]]
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return

    def _remember_generated_wordwolf_pair(self, pair: tuple[str, str]) -> None:
        if pair not in self._saved_wordwolf_pairs:
            self._saved_wordwolf_pairs.append(pair)
            self._save_wordwolf_pairs()

    async def _start_wordwolf_session(
        self,
        interaction: discord.Interaction,
        members: list[discord.Member],
        category: str | None,
        minority_count: int,
        debug_enabled: bool,
    ) -> list[DMResult]:
        assert interaction.guild and isinstance(interaction.channel, discord.TextChannel)
        session = WordWolfSessionState(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            host_user_id=interaction.user.id,
            participant_user_ids=[member.id for member in members],
            category=category,
            minority_count=minority_count,
            debug_enabled=debug_enabled,
        )
        results = await self._run_wordwolf_round(interaction.guild, session)
        control = await interaction.channel.send(self._build_wordwolf_control_text(session, interaction.guild))
        for emoji in (self.WORDWOLF_END_EMOJI, self.WORDWOLF_REPEAT_EMOJI):
            try:
                await control.add_reaction(emoji)
            except Exception:
                pass
        self._wordwolf_sessions[control.id] = session
        return results

    async def _run_wordwolf_round(
        self,
        guild: discord.Guild,
        session: WordWolfSessionState,
    ) -> list[DMResult]:
        members = [guild.get_member(uid) for uid in session.participant_user_ids]
        active_members = [member for member in members if isinstance(member, discord.Member)]
        session.participant_user_ids = [member.id for member in active_members]
        if not active_members:
            session.round_no += 1
            session.common_word = ""
            session.odd_word = ""
            session.minority_user_ids = set()
            session.active = False
            return []
        common, odd = await self._generate_wordwolf_pair(session.category)
        if session.debug_enabled and len(active_members) <= session.minority_count:
            minority_ids = {member.id for member in active_members}
        else:
            minority_ids = {m.id for m in random.sample(active_members, k=session.minority_count)}
        session.round_no += 1
        session.common_word = common
        session.odd_word = odd
        session.minority_user_ids = minority_ids
        session.active = True

        out: list[DMResult] = []
        for member in active_members:
            is_minority = member.id in minority_ids
            word = odd if is_minority else common
            lines = ["🐺 ワードウルフ", f"あなたの単語: **{word}**"]
            if session.debug_enabled:
                role_name = "ワードウルフ" if is_minority else "多数派"
                lines.insert(1, f"あなたの役割: **{role_name}**")
            if is_minority and len(active_members) > 1:
                lines.append(f"少数派人数: {session.minority_count}人")
            lines.extend(
                [
                    "",
                    "進行の目安:",
                    "・単語を直接言わずにヒントを出す",
                    "・全員が1回ずつ話したあとで投票する",
                    "・主催者が ⏹️ を押すと結果を公開する",
                ]
            )
            if session.debug_enabled:
                lines.extend(
                    [
                        "",
                        "Debugモード:",
                        f"・多数派単語: {common}",
                        f"・少数派単語: {odd}",
                        "・1人参加でもDM配布確認のため開始できます",
                    ]
                )
            out.append(await self._safe_dm(member, "\n".join(lines)))
        return out

    async def _run_wordwolf(
        self,
        members: list[discord.Member],
        category: str | None,
        minority_count: int,
        debug_enabled: bool = False,
    ) -> list[DMResult]:
        common, odd = await self._generate_wordwolf_pair(category)
        if debug_enabled and len(members) <= minority_count:
            minority_ids = {m.id for m in members}
        else:
            minority_ids = {m.id for m in random.sample(members, k=minority_count)}
        out: list[DMResult] = []
        for m in members:
            is_minority = m.id in minority_ids
            word = odd if is_minority else common
            lines = ["🐺 ワードウルフ", f"あなたの単語: **{word}**"]
            if debug_enabled:
                role_name = "ワードウルフ" if is_minority else "多数派"
                lines.insert(1, f"あなたの役割: **{role_name}**")
            if is_minority and len(members) > 1:
                lines.append(f"少数派人数: {minority_count}人")
            lines.extend(
                [
                    "",
                    "進行の目安:",
                    "・単語を直接言わずにヒントを出す",
                    "・全員が1回ずつ話したあとで投票する",
                    "・少数派を見つけたら多数派の勝ち",
                ]
            )
            if debug_enabled:
                lines.extend(
                    [
                        "",
                        "Debugモード:",
                        f"・多数派単語: {common}",
                        f"・少数派単語: {odd}",
                        "・1人参加でもDM配布確認のため開始できます",
                    ]
                )
            out.append(await self._safe_dm(m, "\n".join(lines)))
        return out

    async def _run_werewolf(
        self,
        interaction: discord.Interaction,
        members: list[discord.Member],
        debug_enabled: bool = False,
        role_options: WerewolfRoleOptions | None = None,
    ) -> list[DMResult]:
        out: list[DMResult] = []
        active_members: list[discord.Member] = []

        for m in members:
            result = await self._safe_dm(m, "🎮 人狼ゲームの参加確認です。役職DMを送ります。")
            if result.success:
                active_members.append(m)
            else:
                out.append(result)

        role_options = role_options or WerewolfRoleOptions()
        min_players = 1 if debug_enabled else self._minimum_werewolf_players(role_options)
        if len(active_members) < min_players:
            if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
                await interaction.channel.send(
                    f"❌ 人狼を開始できません。DM を受け取れる参加者が {min_players} 人未満でした。DM を許可して再試行してください。"
                )
            return out

        try:
            roles = self._build_werewolf_roles(len(active_members), role_options)
        except ValueError as exc:
            if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
                await interaction.channel.send(f"❌ 人狼を開始できません。{exc}")
            return out
        random.shuffle(roles)
        member_map = {m.id: m for m in active_members}
        started_members: list[discord.Member] = []
        active_role_map: dict[int, str] = {}

        for m, role in zip(active_members, roles):
            result = await self._safe_dm(m, self._build_role_dm_text(role))
            out.append(result)
            if result.success:
                started_members.append(m)
                active_role_map[m.id] = role

        if len(started_members) < min_players:
            if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
                await interaction.channel.send(
                    "❌ 役職DMの送信に失敗したため、人狼ゲームを開始できませんでした。DM 設定を確認して再試行してください。"
                )
            return out

        if len(started_members) != len(active_members):
            try:
                roles = self._build_werewolf_roles(len(started_members), role_options)
            except ValueError as exc:
                if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
                    await interaction.channel.send(f"❌ 人狼を続行できません。{exc}")
                return out
            random.shuffle(roles)
            active_role_map = {m.id: role for m, role in zip(started_members, roles)}
            member_map = {m.id: m for m in started_members}
            for m, role in zip(started_members, roles):
                await self._safe_dm(m, f"🔁 参加者数調整により、最終役職は **{role}** に更新されました。")

        await self._send_role_briefings(member_map, active_role_map)
        await self._start_werewolf_state(interaction, started_members, active_role_map)
        return out

    def _minimum_werewolf_players(self, options: WerewolfRoleOptions) -> int:
        requested_total = sum(value for value in [
            options.wolf_count,
            options.seer_count,
            options.medium_count,
            options.knight_count,
            options.madman_count,
        ] if value is not None)
        return max(4, requested_total) if requested_total > 0 else 4

    def _default_werewolf_role_counts(self, n: int) -> dict[str, int]:
        return {
            "人狼": 1 if n <= 5 else 2 if n <= 11 else 3,
            "占い師": 1 if n >= 4 else 0,
            "霊媒師": 1 if n >= 5 else 0,
            "騎士": 1 if n >= 5 else 0,
            "狂人": 1 if n >= 6 else 0,
        }

    def _build_werewolf_roles(self, n: int, options: WerewolfRoleOptions | None = None) -> list[str]:
        role_counts = self._default_werewolf_role_counts(n)
        options = options or WerewolfRoleOptions()
        overrides = {
            "人狼": options.wolf_count,
            "占い師": options.seer_count,
            "霊媒師": options.medium_count,
            "騎士": options.knight_count,
            "狂人": options.madman_count,
        }
        for role, count in overrides.items():
            if count is not None:
                role_counts[role] = count

        assigned_count = sum(role_counts.values())
        if assigned_count > n:
            raise ValueError("指定した役職人数の合計が参加者数を超えています。")
        if role_counts["人狼"] <= 0:
            raise ValueError("人狼は最低1人必要です。")

        roles: list[str] = []
        for role in ("人狼", "占い師", "霊媒師", "騎士", "狂人"):
            roles.extend([role] * role_counts[role])
        roles.extend(["村人"] * (n - assigned_count))
        return roles[:n]

    def _build_role_dm_text(
        self,
        role: str,
    ) -> str:
        lines = [f"🧩 あなたの役職は **{role}** です。"]
        if role == "人狼":
            lines.append("夜に襲撃先を選んでください。")
        elif role == "占い師":
            lines.append("夜に1人を占い、人狼かどうかを確認できます。")
        elif role == "騎士":
            lines.append("夜に1人を護衛し、その人への襲撃を防げます。")
        elif role == "霊媒師":
            lines.append("昼に処刑された人の役職を知ることができます。")
        elif role == "狂人":
            lines.append("人狼陣営です。会話では村人のふりをしつつ、人狼を勝たせてください。")
        else:
            lines.append("昼は議論と投票で人狼を探してください。")
        return "\n".join(lines)

    async def _send_role_briefings(
        self,
        member_map: dict[int, discord.Member],
        role_map: dict[int, str],
    ) -> None:
        wolves = [uid for uid, role in role_map.items() if role == "人狼"]

        for uid in wolves:
            member = member_map.get(uid)
            if member is None:
                continue
            fellows = [member_map[x].display_name for x in wolves if x != uid and x in member_map]
            if not fellows:
                continue
            try:
                dm = member.dm_channel or await member.create_dm()
                await dm.send(f"🐺 仲間の人狼: {', '.join(fellows)}")
            except Exception:
                continue

    async def _safe_dm(self, member: discord.Member, text: str) -> DMResult:
        try:
            await member.send(text)
            return DMResult(member, True, "")
        except discord.Forbidden:
            return DMResult(member, False, "DM拒否")
        except Exception as e:
            return DMResult(member, False, str(e)[:80])

    async def _start_werewolf_state(
        self,
        interaction: discord.Interaction,
        members: list[discord.Member],
        role_map: dict[int, str],
    ) -> None:
        assert interaction.guild and isinstance(interaction.channel, discord.TextChannel)
        wolves = {uid for uid, role in role_map.items() if role == "人狼"}
        if not wolves:
            return
        state = WerewolfState(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            host_user_id=interaction.user.id,
            alive_user_ids={m.id for m in members},
            roles=role_map,
            wolf_user_ids=wolves,
            action_message_ids={},
            pending_wolf_votes={},
            pending_guard_target=None,
            pending_seer_target=None,
            day_vote_message_id=None,
            day_vote_message_ids={},
            day_vote_candidates=None,
            day_vote_excluded_voter_ids=None,
            pending_day_votes=None,
            day_vote_runoff=False,
            round_no=1,
            medium_result_target=None,
            last_guard_target=None,
        )
        self._werewolf_states[interaction.guild.id] = state
        await interaction.channel.send(
            "🐺 **人狼ゲーム開始**\n"
            "夜に人狼は襲撃先、占い師は占い先、騎士は護衛先をDMリアクションで選びます。"
            "霊媒師は昼に処刑された人の役職を知り、狂人は人狼陣営として行動します。\n"
            "昼の投票も各参加者のDMに届く一覧へリアクションして行います。"
        )
        await self._begin_werewolf_round(interaction.guild, state)

    def _living_madmen(self, state: WerewolfState) -> list[int]:
        return [uid for uid, role in state.roles.items() if role == "狂人" and uid in state.alive_user_ids]

    def _living_wolves(self, state: WerewolfState) -> list[int]:
        return [uid for uid in state.wolf_user_ids if uid in state.alive_user_ids]

    def _living_targets(self, state: WerewolfState) -> list[int]:
        return [uid for uid in state.alive_user_ids if uid not in state.wolf_user_ids]

    def _living_role_users(self, state: WerewolfState, role: str) -> list[int]:
        return [uid for uid, user_role in state.roles.items() if user_role == role and uid in state.alive_user_ids]

    def _living_nonwolves(self, state: WerewolfState) -> list[int]:
        return [uid for uid in state.alive_user_ids if uid not in state.wolf_user_ids]

    async def _begin_werewolf_round(self, guild: discord.Guild, state: WerewolfState) -> None:
        end_text = self._werewolf_end_text(state)
        if end_text:
            await self._announce_werewolf_end(guild, state, end_text)
            return

        state.action_message_ids.clear()
        state.pending_wolf_votes.clear()
        state.pending_guard_target = None
        state.pending_seer_target = None
        state.day_vote_message_id = None
        state.day_vote_message_ids = {}
        state.day_vote_candidates = None
        state.day_vote_excluded_voter_ids = None
        state.pending_day_votes = None
        state.day_vote_runoff = False
        state.medium_result_target = None

        channel = guild.get_channel(state.channel_id)
        if isinstance(channel, discord.TextChannel):
            await channel.send(f"🌙 **夜 {state.round_no}** 各役職はDMを確認してください。")

        await self._send_werewolf_prompt(guild, state)
        await self._send_seer_prompt(guild, state)
        await self._send_knight_prompt(guild, state)

    async def _send_werewolf_prompt(self, guild: discord.Guild, state: WerewolfState) -> None:
        targets = self._living_nonwolves(state)
        if len(targets) > len(self.WEREWOLF_EMOJIS):
            channel = guild.get_channel(state.channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send("人狼対象人数が多すぎるため、現在のDMリアクションUIでは扱えません。")
            self._werewolf_states.pop(guild.id, None)
            return

        lines = [f"🌙 **襲撃対象を選んでください** Round {state.round_no}"]
        emoji_to_target: dict[str, int] = {}
        for idx, uid in enumerate(targets):
            target_member = guild.get_member(uid)
            if target_member is None:
                continue
            emoji = self.WEREWOLF_EMOJIS[idx]
            emoji_to_target[emoji] = uid
            lines.append(f"{emoji} {target_member.display_name}")
        body = "\n".join(lines)

        for wolf_uid in self._living_wolves(state):
            member = guild.get_member(wolf_uid)
            if member is None:
                continue
            try:
                dm = member.dm_channel or await member.create_dm()
                msg = await dm.send(body)
                for emoji in emoji_to_target:
                    await msg.add_reaction(emoji)
                state.action_message_ids[msg.id] = ("wolf", wolf_uid)
            except Exception:
                continue

    async def _send_seer_prompt(self, guild: discord.Guild, state: WerewolfState) -> None:
        seers = self._living_role_users(state, "占い師")
        if not seers:
            return
        targets = [uid for uid in state.alive_user_ids if uid != seers[0]]
        if not targets or len(targets) > len(self.WEREWOLF_EMOJIS):
            return
        lines = [f"🔮 **占う相手を選んでください** Round {state.round_no}"]
        emoji_to_target: dict[str, int] = {}
        for idx, uid in enumerate(targets):
            target_member = guild.get_member(uid)
            if target_member is None:
                continue
            emoji = self.WEREWOLF_EMOJIS[idx]
            emoji_to_target[emoji] = uid
            lines.append(f"{emoji} {target_member.display_name}")
        member = guild.get_member(seers[0])
        if member is None:
            return
        try:
            dm = member.dm_channel or await member.create_dm()
            msg = await dm.send("\n".join(lines))
            for emoji in emoji_to_target:
                await msg.add_reaction(emoji)
            state.action_message_ids[msg.id] = ("seer", seers[0])
        except Exception:
            return

    async def _send_knight_prompt(self, guild: discord.Guild, state: WerewolfState) -> None:
        knights = self._living_role_users(state, "騎士")
        if not knights:
            return
        knight_uid = knights[0]
        targets = self._werewolf_targets_for_actor(state, "knight", knight_uid)
        member = guild.get_member(knight_uid)
        if member is None:
            return
        if not targets:
            try:
                dm = member.dm_channel or await member.create_dm()
                if state.last_guard_target is not None:
                    last_target = guild.get_member(state.last_guard_target)
                    last_name = last_target.display_name if last_target else str(state.last_guard_target)
                    await dm.send(f"🛡️ 今夜は護衛できる対象がいません。前夜に護衛した `{last_name}` は連続で護衛できません。")
                else:
                    await dm.send("🛡️ 今夜は護衛できる対象がいません。")
            except Exception:
                pass
            return
        if len(targets) > len(self.WEREWOLF_EMOJIS):
            return
        lines = [f"🛡️ **護衛相手を選んでください** Round {state.round_no}"]
        emoji_to_target: dict[str, int] = {}
        for idx, uid in enumerate(targets):
            target_member = guild.get_member(uid)
            if target_member is None:
                continue
            emoji = self.WEREWOLF_EMOJIS[idx]
            emoji_to_target[emoji] = uid
            lines.append(f"{emoji} {target_member.display_name}")
        if state.last_guard_target is not None:
            last_target = guild.get_member(state.last_guard_target)
            last_name = last_target.display_name if last_target else str(state.last_guard_target)
            lines.append(f"前夜に護衛した `{last_name}` は今回は選べません。")
        try:
            dm = member.dm_channel or await member.create_dm()
            msg = await dm.send("\n".join(lines))
            for emoji in emoji_to_target:
                await msg.add_reaction(emoji)
            state.action_message_ids[msg.id] = ("knight", knight_uid)
        except Exception:
            return

    def _werewolf_end_text(self, state: WerewolfState) -> str | None:
        wolves = len(self._living_wolves(state))
        madmen = len(self._living_madmen(state))
        villager_side = len(state.alive_user_ids) - wolves - madmen
        if wolves <= 0:
            return "人狼が全滅したため、村人陣営の勝ちです。"
        if villager_side <= 0 or wolves + madmen >= villager_side:
            return "人狼数が村人陣営以上になったため、人狼の勝ちです。"
        return None

    async def _resolve_werewolf_night(self, guild: discord.Guild, state: WerewolfState) -> None:
        if not self._living_wolves(state):
            await self._announce_werewolf_end(guild, state, "人狼が全滅したため、村人陣営の勝ちです。")
            return

        channel = guild.get_channel(state.channel_id)
        lines: list[str] = []

        if state.pending_seer_target is not None:
            seer_uid = next(iter(self._living_role_users(state, "占い師")), None)
            target_uid = state.pending_seer_target
            if seer_uid is not None:
                seer = guild.get_member(seer_uid)
                target = guild.get_member(target_uid)
                result = "人狼" if state.roles.get(target_uid) == "人狼" else "人狼ではありません"
                try:
                    if seer:
                        dm = seer.dm_channel or await seer.create_dm()
                        target_name = target.display_name if target else str(target_uid)
                        await dm.send(f"🔮 `{target_name}` の占い結果: {result}")
                except Exception:
                    pass

        attacked_uid: int | None = None
        tied = False
        votes = list(state.pending_wolf_votes.values())
        if votes:
            counts = {uid: votes.count(uid) for uid in set(votes)}
            top_count = max(counts.values())
            top_targets = [uid for uid, count in counts.items() if count == top_count]
            tied = len(top_targets) > 1
            attacked_uid = random.choice(top_targets)

        if attacked_uid is not None:
            attacked_member = guild.get_member(attacked_uid)
            attacked_name = attacked_member.mention if attacked_member else f"`{attacked_uid}`"
            if tied:
                lines.append("🎲 人狼の投票が同票だったため、対象をランダムに決定しました。")
            if state.pending_guard_target == attacked_uid:
                lines.append(f"🛡️ {attacked_name} は騎士に護衛され、襲撃を防ぎました。")
            else:
                state.alive_user_ids.discard(attacked_uid)
                lines.append(f"☠️ {attacked_name} が襲撃されました。")
        else:
            lines.append("🌙 今夜は人狼の襲撃が成立しませんでした。")

        state.last_guard_target = state.pending_guard_target

        end_text = self._werewolf_end_text(state)
        if isinstance(channel, discord.TextChannel):
            await channel.send("\n".join(lines))
        if end_text:
            await self._announce_werewolf_end(guild, state, end_text)
            return

        await self._start_werewolf_day_vote(guild, state)

    async def _notify_medium_result(self, guild: discord.Guild, state: WerewolfState, target_uid: int) -> None:
        mediums = self._living_role_users(state, "霊媒師")
        if not mediums:
            return
        target = guild.get_member(target_uid)
        target_name = target.display_name if target else str(target_uid)
        role = state.roles.get(target_uid, "不明")
        for medium_uid in mediums:
            medium = guild.get_member(medium_uid)
            if medium is None:
                continue
            try:
                dm = medium.dm_channel or await medium.create_dm()
                await dm.send(f"🪦 霊媒結果: `{target_name}` の役職は **{role}** でした。")
            except Exception:
                continue

    async def _start_werewolf_day_vote(
        self,
        guild: discord.Guild,
        state: WerewolfState,
        candidates: list[int] | None = None,
        excluded_voter_ids: set[int] | None = None,
        runoff: bool = False,
    ) -> None:
        channel = guild.get_channel(state.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        vote_candidates = candidates or sorted(state.alive_user_ids)
        if not vote_candidates:
            await self._announce_werewolf_end(guild, state, "投票対象がいないため終了しました。")
            return
        if len(vote_candidates) > len(self.WEREWOLF_EMOJIS):
            await channel.send("投票対象人数が多すぎるため、現在のリアクションUIでは扱えません。")
            self._werewolf_states.pop(guild.id, None)
            return

        excluded = excluded_voter_ids or set()
        eligible_voters = [uid for uid in state.alive_user_ids if uid not in excluded]
        if not eligible_voters:
            target_uid = random.choice(vote_candidates)
            target_member = guild.get_member(target_uid)
            target_name = target_member.mention if target_member else f"`{target_uid}`"
            await channel.send(f"🎲 再投票できる人がいないため、ランダムで {target_name} を処刑しました。")
            state.alive_user_ids.discard(target_uid)
            await self._notify_medium_result(guild, state, target_uid)
            end_text = self._werewolf_end_text(state)
            if end_text:
                await self._announce_werewolf_end(guild, state, end_text)
                return
            state.round_no += 1
            await self._begin_werewolf_round(guild, state)
            return

        state.day_vote_candidates = vote_candidates
        state.day_vote_excluded_voter_ids = set(excluded)
        state.pending_day_votes = {}
        state.day_vote_runoff = runoff
        state.day_vote_message_id = None
        state.day_vote_message_ids = {}

        prefix = f"☀️ **昼 {state.round_no} 投票**\n"
        voter_text = "生存者全員が投票してください。"
        lines = [prefix + voter_text, "投票したい相手のリアクションを1つ付けてください。"]
        for idx, uid in enumerate(vote_candidates):
            member = guild.get_member(uid)
            if member is None:
                continue
            lines.append(f"{self.WEREWOLF_EMOJIS[idx]} {member.display_name}")

        failed_voters: list[str] = []
        for voter_uid in eligible_voters:
            voter = guild.get_member(voter_uid)
            if voter is None:
                continue
            try:
                dm = voter.dm_channel or await voter.create_dm()
                msg = await dm.send("\n".join(lines))
                for idx in range(len(vote_candidates)):
                    await msg.add_reaction(self.WEREWOLF_EMOJIS[idx])
                state.day_vote_message_ids[msg.id] = voter_uid
            except Exception:
                failed_voters.append(voter.display_name)

        if failed_voters:
            await channel.send(
                "⚠️ 一部参加者に投票DMを送れませんでした: " + ", ".join(failed_voters[:5])
            )

        if not state.day_vote_message_ids:
            target_uid = random.choice(vote_candidates)
            target_member = guild.get_member(target_uid)
            target_name = target_member.mention if target_member else f"`{target_uid}`"
            await channel.send(f"🎲 投票DMを送れなかったため、ランダムで {target_name} を処刑しました。")
            state.alive_user_ids.discard(target_uid)
            await self._notify_medium_result(guild, state, target_uid)
            end_text = self._werewolf_end_text(state)
            if end_text:
                await self._announce_werewolf_end(guild, state, end_text)
                return
            state.round_no += 1
            await self._begin_werewolf_round(guild, state)
            return

        await channel.send("📨 生存者へ投票DMを送信しました。DMのリアクションで投票してください。")

    async def _resolve_werewolf_day_vote(self, guild: discord.Guild, state: WerewolfState) -> None:
        assert state.pending_day_votes is not None
        assert state.day_vote_candidates is not None

        channel = guild.get_channel(state.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        counts = {uid: 0 for uid in state.day_vote_candidates}
        for target_uid in state.pending_day_votes.values():
            if target_uid in counts:
                counts[target_uid] += 1

        top_count = max(counts.values())
        top_targets = [uid for uid, count in counts.items() if count == top_count]

        if len(top_targets) == 1:
            target_uid = top_targets[0]
            target_member = guild.get_member(target_uid)
            target_name = target_member.mention if target_member else f"`{target_uid}`"
            await channel.send(f"⚖️ {target_name} が処刑されました。")
            state.alive_user_ids.discard(target_uid)
            await self._notify_medium_result(guild, state, target_uid)
        else:
            target_uid = random.choice(top_targets)
            target_member = guild.get_member(target_uid)
            target_name = target_member.mention if target_member else f"`{target_uid}`"
            await channel.send(f"🎲 同票だったため、ランダムで {target_name} を処刑しました。")
            state.alive_user_ids.discard(target_uid)
            await self._notify_medium_result(guild, state, target_uid)

        state.day_vote_message_id = None
        state.day_vote_message_ids = {}
        state.day_vote_candidates = None
        state.day_vote_excluded_voter_ids = None
        state.pending_day_votes = None
        state.day_vote_runoff = False

        end_text = self._werewolf_end_text(state)
        if end_text:
            await self._announce_werewolf_end(guild, state, end_text)
            return

        state.round_no += 1
        await self._begin_werewolf_round(guild, state)

    async def _announce_werewolf_end(self, guild: discord.Guild, state: WerewolfState, text: str) -> None:
        channel = guild.get_channel(state.channel_id)
        if isinstance(channel, discord.TextChannel):
            survivors = []
            all_roles = []
            for uid, role in state.roles.items():
                member = guild.get_member(uid)
                if member:
                    status = "生存" if uid in state.alive_user_ids else "死亡"
                    all_roles.append(f"{member.display_name}({role}/{status})")
                    if uid in state.alive_user_ids:
                        survivors.append(f"{member.display_name}({role})")
            tail = f"\n生存者: {', '.join(survivors)}" if survivors else ""
            reveal = f"\n役職公開: {', '.join(all_roles)}" if all_roles else ""
            await channel.send(f"🏁 {text}{tail}{reveal}")
        self._werewolf_states.pop(guild.id, None)

    def _werewolf_targets_for_actor(self, state: WerewolfState, role: str, actor_uid: int) -> list[int]:
        if role == "wolf":
            return self._living_nonwolves(state)
        if role == "seer":
            return [uid for uid in state.alive_user_ids if uid != actor_uid]
        if role == "knight":
            return [uid for uid in state.alive_user_ids if uid != state.last_guard_target]
        return []

    async def _maybe_resolve_werewolf_night(self, guild: discord.Guild, state: WerewolfState) -> None:
        living_wolves = self._living_wolves(state)
        seers = self._living_role_users(state, "占い師")
        knights = self._living_role_users(state, "騎士")
        wolves_ready = len(state.pending_wolf_votes) >= len(living_wolves)
        seer_ready = not seers or state.pending_seer_target is not None
        knight_targets = self._werewolf_targets_for_actor(state, "knight", knights[0]) if knights else []
        knight_ready = not knights or state.pending_guard_target is not None or not knight_targets
        if wolves_ready and seer_ready and knight_ready:
            await self._resolve_werewolf_night(guild, state)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        emoji = str(payload.emoji)

        lobby = self._game_lobbies.get(payload.message_id)
        if lobby is not None:
            if emoji == self.JOIN_EMOJI:
                await self._refresh_game_lobby(payload.message_id)
                return
            if emoji == self.START_EMOJI and payload.user_id == lobby.host_user_id:
                return

        session = self._wordwolf_sessions.get(payload.message_id)
        if session is not None:
            if payload.user_id != session.host_user_id or payload.guild_id != session.guild_id:
                return
            guild = self.bot.get_guild(session.guild_id)
            channel = guild.get_channel(session.channel_id) if guild else None
            if guild is None or not isinstance(channel, discord.TextChannel):
                return
            try:
                message = await channel.fetch_message(payload.message_id)
            except Exception:
                return

            if emoji == self.WORDWOLF_END_EMOJI and session.active:
                session.active = False
                await message.edit(content=self._build_wordwolf_result_text(session, guild))
                return
            if emoji == self.WORDWOLF_REPEAT_EMOJI and not session.active:
                results = await self._run_wordwolf_round(guild, session)
                await message.edit(content=self._build_wordwolf_control_text(session, guild))
                ok_count = sum(1 for result in results if result.success)
                ng = [f"{result.user.mention} ({result.reason})" for result in results if not result.success]
                notice = f"🔁 同じメンバーでワードウルフを再配布しました。DM成功 {ok_count}/{len(results)}"
                if ng:
                    notice += "\nDM失敗: " + ", ".join(ng[:5])
                await channel.send(notice, delete_after=15)
                return

        if emoji not in self.WEREWOLF_EMOJIS:
            return

        for guild_id, state in list(self._werewolf_states.items()):
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue

            day_vote_voter_uid = (state.day_vote_message_ids or {}).get(payload.message_id)
            if day_vote_voter_uid is not None:
                if (
                    payload.user_id != day_vote_voter_uid
                    or payload.user_id not in state.alive_user_ids
                    or state.day_vote_candidates is None
                    or state.pending_day_votes is None
                ):
                    return
                excluded = state.day_vote_excluded_voter_ids or set()
                if payload.user_id in excluded:
                    return
                target_uid = None
                for idx, uid in enumerate(state.day_vote_candidates):
                    if self.WEREWOLF_EMOJIS[idx] == emoji:
                        target_uid = uid
                        break
                if target_uid is None:
                    return
                state.pending_day_votes[payload.user_id] = target_uid
                delivered_voter_count = len(set((state.day_vote_message_ids or {}).values()))
                if len(state.pending_day_votes) >= delivered_voter_count:
                    await self._resolve_werewolf_day_vote(guild, state)
                return

            action = state.action_message_ids.get(payload.message_id)
            if action is None:
                continue
            role, actor_uid = action
            if actor_uid != payload.user_id:
                continue
            targets = self._werewolf_targets_for_actor(state, role, actor_uid)
            if len(targets) > len(self.WEREWOLF_EMOJIS):
                continue
            target_uid = None
            for idx, uid in enumerate(targets):
                if self.WEREWOLF_EMOJIS[idx] == emoji:
                    target_uid = uid
                    break
            if target_uid is None:
                continue

            if role == "wolf":
                state.pending_wolf_votes[actor_uid] = target_uid
                confirm = "襲撃対象"
            elif role == "seer":
                state.pending_seer_target = target_uid
                confirm = "占い対象"
            elif role == "knight":
                state.pending_guard_target = target_uid
                confirm = "護衛対象"
            else:
                continue

            member = guild.get_member(actor_uid)
            try:
                if member:
                    dm = member.dm_channel or await member.create_dm()
                    target_member = guild.get_member(target_uid)
                    target_name = target_member.display_name if target_member else str(target_uid)
                    await dm.send(f"✅ {confirm}を `{target_name}` に設定しました。")
            except Exception:
                pass

            await self._maybe_resolve_werewolf_night(guild, state)
            return

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return
        if str(payload.emoji) != self.JOIN_EMOJI:
            return
        if payload.message_id not in self._game_lobbies:
            return
        await self._refresh_game_lobby(payload.message_id)

    async def _generate_words(self, count: int, category: str | None) -> list[str]:
        cat = strip_ansi_and_ctrl((category or "").strip()).replace("@", "＠")[:80]
        cat_line = f"カテゴリ: {cat}\n" if cat else ""
        prompt = get_prompt("games", "generate_words_prompt").format(
            category_line=cat_line,
            count=count,
        )
        raw = await self._ask_ollama(prompt)
        words = self._parse_json_list(raw)
        words = [w.strip() for w in words if isinstance(w, str) and w.strip()]
        if len(words) < count:
            fallback = ["りんご", "電車", "海", "雲", "カメラ", "猫", "図書館", "時計", "キャンプ", "花火"]
            pool = words + [w for w in fallback if w not in words]
            while len(pool) < count:
                pool.append(f"単語{len(pool)+1}")
            words = pool[:count]
        return words[:count]

    async def _generate_wordwolf_pair(self, category: str | None) -> tuple[str, str]:
        cat = strip_ansi_and_ctrl((category or "").strip()).replace("@", "＠")[:80]
        cat_line = f"カテゴリは「{cat}」です。" if cat else ""
        prompt = get_prompt("games", "generate_wordwolf_pair_prompt").format(
            category_line=cat_line,
        )
        raw = await self._ask_ollama(prompt)
        try:
            obj = json.loads(raw)
            c = str(obj.get("common", "")).strip()
            o = str(obj.get("odd", "")).strip()
            if c and o and c != o:
                pair = (c, o)
                if pair not in self._recent_wordwolf_pairs:
                    self._remember_generated_wordwolf_pair(pair)
                    self._remember_wordwolf_pair(pair)
                    return pair
        except Exception:
            pass
        fallback_pairs = [
            ("コーヒー", "紅茶"),
            ("犬", "猫"),
            ("ライオン", "トラ"),
            ("スマホ", "タブレット"),
            ("電車", "バス"),
            ("うどん", "そば"),
            ("海", "川"),
            ("映画", "ドラマ"),
            ("自転車", "バイク"),
            ("りんご", "なし"),
            ("学校", "塾"),
            ("春", "秋"),
            ("サッカー", "フットサル"),
            ("漫画", "アニメ"),
            ("寿司", "刺身"),
            ("雪", "雨"),
            ("山", "丘"),
            ("夜", "夕方"),
            ("机", "テーブル"),
            ("病院", "薬局"),
            ("温泉", "プール"),
            ("ギター", "ベース"),
            ("ケーキ", "プリン"),
            ("ラーメン", "パスタ"),
            ("ハンバーガー", "ホットドッグ"),
            ("りす", "うさぎ"),
            ("ペン", "えんぴつ"),
            ("本", "雑誌"),
            ("ピアノ", "オルガン"),
            ("洗濯機", "冷蔵庫"),
            ("パン", "おにぎり"),
            ("スキー", "スノボ"),
            ("プリンター", "スキャナー"),
            ("帽子", "ヘルメット"),
            ("鳥", "魚"),
            ("花", "木"),
            ("月", "星"),
            ("カメラ", "ビデオカメラ"),
            ("ホテル", "旅館"),
            ("公園", "遊園地"),
            ("ビール", "ワイン"),
            ("チョコ", "クッキー"),
            ("バナナ", "みかん"),
            ("椅子", "ソファ"),
        ]
        category_hints: list[tuple[tuple[str, ...], list[tuple[str, str]]]] = [
            (("動物", "どうぶつ", "アニマル"), [("犬", "猫"), ("ライオン", "トラ"), ("イルカ", "クジラ"), ("りす", "うさぎ"), ("鳥", "魚")]),
            (("食べ物", "飲み物", "グルメ"), [("コーヒー", "紅茶"), ("うどん", "そば"), ("カレー", "シチュー"), ("パン", "おにぎり"), ("ハンバーガー", "ホットドッグ"), ("ビール", "ワイン"), ("チョコ", "クッキー"), ("バナナ", "みかん")]),
            (("乗り物", "交通"), [("電車", "バス"), ("自転車", "バイク"), ("飛行機", "新幹線")]),
            (("家電", "機械", "ガジェット"), [("スマホ", "タブレット"), ("パソコン", "テレビ"), ("イヤホン", "ヘッドホン"), ("洗濯機", "冷蔵庫"), ("プリンター", "スキャナー"), ("カメラ", "ビデオカメラ")]),
            (("場所", "施設", "旅行"), [("学校", "塾"), ("病院", "薬局"), ("温泉", "プール"), ("ホテル", "旅館"), ("公園", "遊園地")]),
            (("天気", "自然"), [("雪", "雨"), ("海", "川"), ("山", "丘"), ("花", "木"), ("月", "星")]),
            (("娯楽", "エンタメ", "音楽"), [("映画", "ドラマ"), ("漫画", "アニメ"), ("ギター", "ベース"), ("ピアノ", "オルガン")]),
            (("文房具", "学校用品"), [("ペン", "えんぴつ"), ("本", "雑誌")]),
            (("スポーツ", "運動"), [("サッカー", "フットサル"), ("スキー", "スノボ")]),
            (("家具", "部屋"), [("机", "テーブル"), ("椅子", "ソファ"), ("帽子", "ヘルメット")]),
        ]
        fallback_pairs.extend(self._saved_wordwolf_pairs)
        lowered = cat.lower()
        for keywords, pairs in category_hints:
            if any(keyword in cat or keyword in lowered for keyword in keywords):
                candidates = [pair for pair in pairs if pair not in self._recent_wordwolf_pairs]
                pair = random.choice(candidates or pairs)
                self._remember_wordwolf_pair(pair)
                return pair
        candidates = [pair for pair in fallback_pairs if pair not in self._recent_wordwolf_pairs]
        pair = random.choice(candidates or fallback_pairs)
        self._remember_wordwolf_pair(pair)
        return pair

    def _remember_wordwolf_pair(self, pair: tuple[str, str]) -> None:
        self._recent_wordwolf_pairs.append(pair)
        if len(self._recent_wordwolf_pairs) > 20:
            self._recent_wordwolf_pairs = self._recent_wordwolf_pairs[-20:]

    async def _ask_ollama(self, prompt: str) -> str:
        try:
            model_default = str(_settings.get("ollama.model_default", "gpt-oss:120b"))
            text = self.bot.ollama_client.chat_simple(
                model=model_default,
                prompt=prompt,
                stream=False,
            )
            return (text or "").strip()
        except Exception:
            return ""

    def _parse_json_list(self, raw: str) -> list[str]:
        raw = (raw or "").strip()
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                return [str(x) for x in arr]
        except Exception:
            pass
        # JSON以外の返答に最低限対応
        lines = [ln.strip(" -・\t") for ln in raw.splitlines() if ln.strip()]
        return [ln for ln in lines if ln]

    async def _start_aiueo_battle(self, interaction: discord.Interaction, participants: list[discord.Member]) -> None:
        assert interaction.guild and isinstance(interaction.channel, discord.TextChannel)
        if interaction.channel.id in self._aiueo_states:
            await interaction.followup.send("このチャンネルではすでにあいうえおバトルが進行中です。", ephemeral=True)
            return

        await interaction.followup.send(
            "あいうえおバトルを開始します。参加者はDMで7文字以内の単語を送ってください（120秒以内）。",
            ephemeral=True,
        )
        await interaction.channel.send(
            "📝 参加者にDMを送りました。120秒以内に単語を送ってください。"
        )

        submissions = await asyncio.gather(
            *(self._collect_secret_word(m, timeout_sec=120) for m in participants)
        )
        words: dict[int, str] = {}
        failed: list[str] = []
        for member, word in zip(participants, submissions):
            if not word:
                failed.append(member.mention)
            else:
                words[member.id] = word

        if len(words) < 1:
            await interaction.channel.send(
                "❌ 有効な単語提出者がいなかったため中止しました。\n"
                f"未提出/無効: {', '.join(failed) if failed else 'なし'}"
            )
            return

        order = list(words.keys())
        random.shuffle(order)
        state = AiueoBattleState(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            host_user_id=interaction.user.id,
            turn_user_ids=order,
            active_user_ids=set(order),
            secret_words=words,
            used_chars=set(),
            revealed_chars={uid: set() for uid in order},
            turn_index=0,
            attacks_used_in_turn=0,
        )
        self._aiueo_states[interaction.channel.id] = state

        board = self._render_aiueo_board(state.used_chars)
        status = self._render_aiueo_status(interaction.guild, state)
        first_uid = self._current_turn_user_id(state)
        await interaction.channel.send(
            "🎯 **あいうえおバトル開始**\n"
            "現在ターンの人がチャンネルで1文字の50音を送ると攻撃になります\n"
            "ヒットしたら同じターンでもう1回だけ攻撃できます（最大2回）\n"
            "`/game mode:あいうえおバトル action:状況表示` で状況確認\n"
            f"\n{board}\n\n{status}\n\n⚔️ <@{first_uid}> の攻撃です。"
        )

    async def _collect_secret_word(self, member: discord.Member, timeout_sec: int = 120) -> Optional[str]:
        try:
            await member.send(
                "📝 あいうえおバトルの単語を送ってください。\n"
                "条件: 7文字以内、ひらがなのみ（50音のみ）"
            )
        except Exception:
            return None

        def check(m: discord.Message) -> bool:
            return m.author.id == member.id and isinstance(m.channel, discord.DMChannel)

        deadline = asyncio.get_event_loop().time() + timeout_sec
        while True:
            remain = deadline - asyncio.get_event_loop().time()
            if remain <= 0:
                return None
            try:
                msg = await self.bot.wait_for("message", timeout=remain, check=check)
            except asyncio.TimeoutError:
                return None
            w = (msg.content or "").strip()
            if self._is_valid_aiueo_word(w):
                try:
                    await member.send(f"✅ 受付しました: `{w}`")
                except Exception:
                    pass
                return w
            try:
                await member.send("❌ 条件不一致です。7文字以内・50音のみで再入力してください。")
            except Exception:
                pass

    def _is_valid_aiueo_word(self, word: str) -> bool:
        if not word or len(word) > 7:
            return False
        for ch in word:
            if ch not in self.AIUEO_ALLOWED:
                return False
        return True

    def _render_aiueo_board(self, used_chars: set[str]) -> str:
        lines: list[str] = []
        for row in self.AIUEO_ROWS:
            cols = []
            for ch in row:
                cols.append(f"({ch})" if ch in used_chars else ch)
            lines.append(" ".join(cols))
        return "\n".join(lines)

    def _render_aiueo_status(self, guild: discord.Guild, state: AiueoBattleState) -> str:
        rows: list[str] = []
        for uid in state.turn_user_ids:
            m = guild.get_member(uid)
            name = m.display_name if isinstance(m, discord.Member) else str(uid)
            alive = uid in state.active_user_ids
            w = state.secret_words[uid]
            revealed = state.revealed_chars[uid]
            mask = "".join([c if c in revealed else "□" for c in w])
            mark = "🟢" if alive else "❌"
            rows.append(f"{mark} {name}: {mask}")
        return "\n".join(rows)

    def _current_turn_user_id(self, state: AiueoBattleState) -> int:
        # turn_index は active を指すまで進める
        n = len(state.turn_user_ids)
        for _ in range(max(1, n)):
            uid = state.turn_user_ids[state.turn_index % n]
            if uid in state.active_user_ids:
                return uid
            state.turn_index = (state.turn_index + 1) % n
        return state.turn_user_ids[state.turn_index % n]

    def _advance_turn(self, state: AiueoBattleState) -> None:
        n = len(state.turn_user_ids)
        state.attacks_used_in_turn = 0
        for _ in range(max(1, n)):
            state.turn_index = (state.turn_index + 1) % n
            uid = state.turn_user_ids[state.turn_index]
            if uid in state.active_user_ids:
                return

    def _validate_aiueo_attack_char(self, state: AiueoBattleState, ch: str) -> str | None:
        if len(ch) != 1:
            return "1文字だけ指定してください。"
        if ch not in self.AIUEO_ALLOWED:
            return "50音の1文字だけ使用できます。"
        if ch in state.used_chars:
            return "その文字はすでに使用済みです。"
        return None

    def _apply_aiueo_attack(
        self,
        guild: discord.Guild,
        channel_id: int,
        state: AiueoBattleState,
        attacker_uid: int,
        ch: str,
    ) -> tuple[str, bool]:
        state.used_chars.add(ch)
        state.attacks_used_in_turn += 1
        hit_players: list[int] = []
        eliminated: list[int] = []

        for uid in list(state.active_user_ids):
            w = state.secret_words[uid]
            if ch in w:
                state.revealed_chars[uid].add(ch)
                hit_players.append(uid)
                if set(w) <= state.revealed_chars[uid]:
                    state.active_user_ids.discard(uid)
                    eliminated.append(uid)

        board = self._render_aiueo_board(state.used_chars)
        status = self._render_aiueo_status(guild, state)
        lines = [
            f"✅ <@{attacker_uid}> の `{ch}` 攻撃。",
            f"ヒット人数: {len(hit_players)}",
            f"脱落: {', '.join([f'<@{u}>' for u in eliminated]) if eliminated else 'なし'}",
            "",
            board,
            "",
            status,
        ]

        if len(state.active_user_ids) <= 1:
            winner_id = next(iter(state.active_user_ids)) if state.active_user_ids else None
            if winner_id:
                lines.append(f"\n🏆 勝者: <@{winner_id}>")
            else:
                lines.append("\n🏁 全員脱落で終了")
            return "\n".join(lines), True

        got_extra_attack = bool(hit_players) and state.attacks_used_in_turn < 2
        if got_extra_attack:
            lines.append(f"\n⚔️ <@{attacker_uid}> の攻撃です。もう1文字どうぞ。")
            return "\n".join(lines), False

        self._advance_turn(state)
        next_uid = self._current_turn_user_id(state)
        lines.append(f"\n⚔️ <@{next_uid}> の攻撃です。")
        return "\n".join(lines), False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not isinstance(message.channel, discord.TextChannel):
            return

        state = self._aiueo_states.get(message.channel.id)
        if not state or state.guild_id != message.guild.id:
            return
        if message.author.id != self._current_turn_user_id(state):
            return

        ch = (message.content or "").strip()
        if len(ch) != 1:
            return

        error = self._validate_aiueo_attack_char(state, ch)
        if error:
            await message.channel.send(error)
            return

        text, ended = self._apply_aiueo_attack(message.guild, message.channel.id, state, message.author.id, ch)
        await message.channel.send(text)
        if ended:
            self._aiueo_states.pop(message.channel.id, None)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            text = f"連続実行を制限中です。{error.retry_after:.1f}秒後に再試行してください。"
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
            return
        raise error
