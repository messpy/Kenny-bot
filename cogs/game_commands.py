# cogs/game_commands.py
# ミニゲーム用スラッシュコマンド

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.runtime_settings import get_settings
from utils.text import strip_ansi_and_ctrl

_settings = get_settings()


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


class GameCommands(commands.Cog):
    """ミニゲームコマンド"""

    JOIN_EMOJI = "🎮"
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
        "わ",
        "がぎぐげご",
        "ざじずぜぞ",
        "だぢづでど",
        "ばびぶべぼ",
        "ぱぴぷぺぽ",
        "ぁぃぅぇぉ",
        "ゃゅょっ",
        "ー",
    ]
    AIUEO_ALLOWED = set("".join(AIUEO_ROWS))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._aiueo_states: dict[int, AiueoBattleState] = {}

    @app_commands.command(name="game", description="ミニゲームを開始（リアクション参加）")
    @app_commands.checks.cooldown(1, 20.0)
    @app_commands.describe(
        mode="ゲーム種類",
        action="操作（あいうえおバトル時: 開始/文字宣言/状況/終了）",
        char="あいうえおバトルで宣言する1文字（action=文字宣言）",
        join_seconds="参加受付秒数（10〜60秒）",
        category="任意カテゴリ（例: 動物, 食べ物, 映画）",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="ランダム数字(0-100)", value="number"),
            app_commands.Choice(name="単語配布", value="words"),
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
    )
    async def game(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
        action: app_commands.Choice[str] | None = None,
        char: str | None = None,
        join_seconds: app_commands.Range[int, 10, 60] = 20,
        category: str | None = None,
    ):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("サーバーのテキストチャンネルで実行してください。", ephemeral=True)
            return

        action_value = action.value if action else "start"

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
            f"🎲 **{mode.name}** を開始します。\n"
            f"{self.JOIN_EMOJI} を押した人が参加者です。\n"
            f"受付終了まで: {join_seconds}秒"
        )
        await recruit.add_reaction(self.JOIN_EMOJI)

        remain = int(join_seconds)
        while remain > 10:
            await asyncio.sleep(10)
            remain -= 10
            await recruit.edit(
                content=(
                    f"🎲 **{mode.name}** を開始します。\n"
                    f"{self.JOIN_EMOJI} を押した人が参加者です。\n"
                    f"受付終了まで: {remain}秒"
                )
            )

        # 最後の10秒は1秒ごとに更新
        for remain in range(remain - 1, -1, -1):
            await asyncio.sleep(1)
            if remain > 0:
                await recruit.edit(
                    content=(
                        f"🎲 **{mode.name}** を開始します。\n"
                        f"{self.JOIN_EMOJI} を押した人が参加者です。\n"
                        f"受付終了まで: {remain}秒"
                    )
                )
            else:
                await recruit.edit(
                    content=(
                        f"🎲 **{mode.name}** 参加受付を終了しました。\n"
                        f"{self.JOIN_EMOJI} で参加したユーザーを集計中..."
                    )
                )

        recruit = await interaction.channel.fetch_message(recruit.id)
        participants = await self._collect_participants(recruit, interaction.guild)

        min_players = 1 if mode.value in ("number", "words") else 2
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
            results = await self._run_wordwolf(participants, category)
            title = "ワードウルフ配布をDM送信しました"
        else:
            results = await self._run_werewolf(participants)
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
        if len(ch) != 1:
            await interaction.response.send_message("1文字だけ指定してください。", ephemeral=True)
            return
        if ch not in self.AIUEO_ALLOWED:
            await interaction.response.send_message("使用できない文字です。", ephemeral=True)
            return
        if ch in state.used_chars:
            await interaction.response.send_message("その文字はすでに使用済みです。", ephemeral=True)
            return

        state.used_chars.add(ch)
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

        # 勝敗判定
        if len(state.active_user_ids) <= 1:
            winner_id = next(iter(state.active_user_ids)) if state.active_user_ids else None
            board = self._render_aiueo_board(state.used_chars)
            status = self._render_aiueo_status(interaction.guild, state)
            lines = [
                f"✅ `{ch}` を宣言しました。",
                f"ヒット人数: {len(hit_players)}",
                f"脱落: {', '.join([f'<@{u}>' for u in eliminated]) if eliminated else 'なし'}",
                "",
                board,
                "",
                status,
            ]
            if winner_id:
                lines.append(f"\n🏆 勝者: <@{winner_id}>")
            else:
                lines.append("\n🏁 全員脱落で終了")
            await interaction.response.send_message("\n".join(lines))
            self._aiueo_states.pop(interaction.channel.id, None)
            return

        # 次ターンへ
        self._advance_turn(state)
        next_uid = self._current_turn_user_id(state)
        board = self._render_aiueo_board(state.used_chars)
        status = self._render_aiueo_status(interaction.guild, state)
        await interaction.response.send_message(
            f"✅ `{ch}` を宣言しました。\n"
            f"ヒット人数: {len(hit_players)}\n"
            f"脱落: {', '.join([f'<@{u}>' for u in eliminated]) if eliminated else 'なし'}\n"
            f"次のターン: <@{next_uid}>\n\n{board}\n\n{status}"
        )

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

    async def _run_wordwolf(self, members: list[discord.Member], category: str | None) -> list[DMResult]:
        common, odd = await self._generate_wordwolf_pair(category)
        wolf = random.choice(members)
        out: list[DMResult] = []
        for m in members:
            word = odd if m.id == wolf.id else common
            note = "（あなたは少数派です）" if m.id == wolf.id else ""
            out.append(await self._safe_dm(m, f"🐺 ワードウルフのお題: **{word}** {note}"))
        return out

    async def _run_werewolf(self, members: list[discord.Member]) -> list[DMResult]:
        roles = self._build_werewolf_roles(len(members))
        random.shuffle(roles)
        out: list[DMResult] = []
        for m, role in zip(members, roles):
            out.append(await self._safe_dm(m, f"🧩 あなたの役職は **{role}** です。"))
        return out

    def _build_werewolf_roles(self, n: int) -> list[str]:
        wolves = 1 if n <= 5 else 2 if n <= 9 else 3
        roles = ["人狼"] * wolves
        base = ["占い師", "騎士", "霊媒師"]
        for r in base:
            if len(roles) < n:
                roles.append(r)
        while len(roles) < n:
            roles.append("村人")
        return roles[:n]

    async def _safe_dm(self, member: discord.Member, text: str) -> DMResult:
        try:
            await member.send(text)
            return DMResult(member, True, "")
        except discord.Forbidden:
            return DMResult(member, False, "DM拒否")
        except Exception as e:
            return DMResult(member, False, str(e)[:80])

    async def _generate_words(self, count: int, category: str | None) -> list[str]:
        cat = strip_ansi_and_ctrl((category or "").strip()).replace("@", "＠")[:80]
        cat_line = f"カテゴリ: {cat}\n" if cat else ""
        prompt = (
            f"{cat_line}"
            f"{count}個の日本語の短い名詞を重複なしで出力してください。"
            "JSON配列のみで返すこと。例: [\"りんご\",\"電車\"]"
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
        prompt = (
            "ワードウルフ用に、似ているが異なる日本語の単語ペアを1組作ってください。"
            f"{cat_line}"
            "JSONオブジェクトのみで返すこと。"
            "形式: {\"common\":\"...\",\"odd\":\"...\"}"
        )
        raw = await self._ask_ollama(prompt)
        try:
            obj = json.loads(raw)
            c = str(obj.get("common", "")).strip()
            o = str(obj.get("odd", "")).strip()
            if c and o and c != o:
                return c, o
        except Exception:
            pass
        return "犬", "オオカミ"

    async def _ask_ollama(self, prompt: str) -> str:
        try:
            model_default = str(_settings.get("ollama.model_default", "gpt-oss:120b-cloud"))
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

        words: dict[int, str] = {}
        failed: list[str] = []
        for m in participants:
            w = await self._collect_secret_word(m, timeout_sec=120)
            if not w:
                failed.append(m.mention)
            else:
                words[m.id] = w

        if len(words) < 2:
            await interaction.channel.send(
                "❌ 有効な単語提出者が2人未満だったため中止しました。\n"
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
        )
        self._aiueo_states[interaction.channel.id] = state

        board = self._render_aiueo_board(state.used_chars)
        status = self._render_aiueo_status(interaction.guild, state)
        first_uid = self._current_turn_user_id(state)
        await interaction.channel.send(
            "🎯 **あいうえおバトル開始**\n"
            "コマンド: `/game mode:あいうえおバトル action:文字宣言` で1文字宣言\n"
            "`/game mode:あいうえおバトル action:状況表示` で状況確認\n"
            f"最初のターン: <@{first_uid}>\n\n{board}\n\n{status}"
        )

    async def _collect_secret_word(self, member: discord.Member, timeout_sec: int = 120) -> Optional[str]:
        try:
            await member.send(
                "📝 あいうえおバトルの単語を送ってください。\n"
                "条件: 7文字以内、ひらがな（濁点/小文字/ー可、を/ん不可）"
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
                await member.send("❌ 条件不一致です。7文字以内・使用可能文字のみで再入力してください。")
            except Exception:
                pass

    def _is_valid_aiueo_word(self, word: str) -> bool:
        if not word or len(word) > 7:
            return False
        for ch in word:
            if ch not in self.AIUEO_ALLOWED:
                return False
        if "を" in word or "ん" in word:
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
        for _ in range(max(1, n)):
            state.turn_index = (state.turn_index + 1) % n
            uid = state.turn_user_ids[state.turn_index]
            if uid in state.active_user_ids:
                return

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            text = f"連続実行を制限中です。{error.retry_after:.1f}秒後に再試行してください。"
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
            return
        raise error
