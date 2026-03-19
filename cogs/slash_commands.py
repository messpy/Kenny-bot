# cogs/slash_commands.py
# スラッシュコマンド集

import asyncio
import subprocess
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import List

import discord
from discord import app_commands
from discord.app_commands import checks
from discord.ext import commands

from utils.build_info import load_build_info
from utils.runtime_settings import get_settings

JST = timezone(timedelta(hours=9))
_settings = get_settings()
ReadableChannel = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.Thread


@dataclass
class VcPanelState:
    guild_id: int
    channel_id: int
    voice_channel_id: int
    host_user_id: int
    joined_user_ids: set[int] = field(default_factory=set)


class SlashCommands(commands.Cog):
    """スラッシュコマンド"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._started_at = discord.utils.utcnow()
        # message_id -> (seconds, title)
        self._timer_restart_templates: dict[int, tuple[int, str]] = {}
        # message_id -> vc panel state
        self._vc_panels: dict[int, VcPanelState] = {}

    VC_JOIN_EMOJI = "✅"
    VC_MUTE_ON_EMOJI = "🔇"
    VC_MUTE_OFF_EMOJI = "🎤"
    VC_DEAF_ON_EMOJI = "🙉"
    VC_DEAF_OFF_EMOJI = "🙊"

    _CONFIG_CHOICES = [
        app_commands.Choice(name="会話履歴の参照行数", value="chat.history_lines"),
        app_commands.Choice(name="履歴保存の最大件数", value="chat.history_max_messages"),
        app_commands.Choice(name="履歴保存日数", value="chat.history_retention_days"),
        app_commands.Choice(name="AI返信の最大文字数", value="chat.max_response_length"),
        app_commands.Choice(name="プロンプト文字数上限", value="chat.max_response_length_prompt"),
        app_commands.Choice(name="kenny-chat発言クールダウン秒", value="kenny_chat.cooldown_seconds"),
        app_commands.Choice(name="要約の既定件数", value="summarize_recent_default_messages"),
        app_commands.Choice(name="要約の履歴取得件数", value="summarize_recent.history_fetch_limit"),
        app_commands.Choice(name="要約の投入行数上限", value="summarize_recent.transcript_lines_limit"),
        app_commands.Choice(name="要約の最大件数", value="summarize_recent.max_messages"),
        app_commands.Choice(name="既定Ollamaモデル", value="ollama.model_default"),
        app_commands.Choice(name="要約Ollamaモデル", value="ollama.model_summary"),
        app_commands.Choice(name="Ollamaタイムアウト秒", value="ollama.timeout_sec"),
        app_commands.Choice(name="議事録リアルタイム翻訳", value="meeting.realtime_translation_enabled"),
        app_commands.Choice(name="議事録文字起こしプロバイダ", value="meeting.transcription_provider"),
        app_commands.Choice(name="Google STT 言語コード", value="meeting.google_language_code"),
        app_commands.Choice(name="Google STT 分割秒数", value="meeting.google_chunk_seconds"),
        app_commands.Choice(name="Google STT タイムアウト秒", value="meeting.google_timeout_sec"),
        app_commands.Choice(name="Google STT モデル", value="meeting.google_model"),
        app_commands.Choice(name="AI同時実行数", value="security.ai_max_concurrency"),
        app_commands.Choice(name="AIチャンネル間隔秒", value="security.ai_channel_cooldown_seconds"),
        app_commands.Choice(name="AI入力最大文字数", value="security.max_user_message_chars"),
        app_commands.Choice(name="kenny-chat招待URL/全体メンション禁止", value="kenny_chat.block_invite_and_mass_mention"),
        app_commands.Choice(name="スパム許容メッセージ数", value="security.spam.max_msgs"),
        app_commands.Choice(name="スパム判定秒数", value="security.spam.per_seconds"),
    ]

    _INT_KEYS = {
        "chat.history_lines",
        "chat.history_max_messages",
        "chat.history_retention_days",
        "chat.max_response_length",
        "chat.max_response_length_prompt",
        "kenny_chat.cooldown_seconds",
        "summarize_recent_default_messages",
        "summarize_recent.history_fetch_limit",
        "summarize_recent.transcript_lines_limit",
        "summarize_recent.max_messages",
        "ollama.timeout_sec",
        "meeting.max_minutes",
        "meeting.audio_max_total_mb",
        "meeting.audio_max_user_mb",
        "meeting.google_chunk_seconds",
        "meeting.google_timeout_sec",
        "security.ai_max_concurrency",
        "security.ai_channel_cooldown_seconds",
        "security.max_user_message_chars",
        "security.spam.max_msgs",
        "security.spam.per_seconds",
        "security.spam.max_ai_calls",
        "security.spam.ai_per_seconds",
        "security.spam.dup_window_seconds",
        "security.spam.warn_cooldown_seconds",
    }
    _BOOL_KEYS = {
        "kenny_chat.block_invite_and_mass_mention",
        "meeting.realtime_translation_enabled",
    }

    @staticmethod
    def _is_readable_channel(channel: object) -> bool:
        return isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread))

    @app_commands.command(name="help", description="Botで使える機能とコマンドを表示")
    async def slash_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Kenny Bot 使い方",
            description="このBotで使える主な機能です。",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="会話機能",
            value=(
                "- Botへのメンション/返信でAI応答\n"
                "- DMでもそのままAI会話可能\n"
                "- 会話時は直近100件の履歴を参照\n"
                "- キーワード自動リアクション\n"
                "- スパム検知と自動処罰"
            ),
            inline=False,
        )
        embed.add_field(
            name="議事録機能",
            value=(
                "- VC参加者が `/minutes_start` で開始\n"
                "- `/minutes_stop` またはVC無人で停止\n"
                "- Google Speech-to-Text を優先して文字起こし\n"
                "- Google失敗時だけ faster-whisper にフォールバック\n"
                "- 音声を文字起こしし、長文はAI要約して投稿\n"
                "- 投稿時はコマンド実行者をメンション"
            ),
            inline=False,
        )
        embed.add_field(
            name="kenny-chat 連携",
            value=(
                "- 各サーバーに `kenny-chat` チャンネルを作ると相互中継\n"
                "- 表示名は発言者の頭文字のみ\n"
                "- 12秒に1回まで発言可能\n"
                "- 元発言を削除すると中継先の投稿も削除"
            ),
            inline=False,
        )
        embed.add_field(
            name="ログ機能",
            value=(
                "- `voice-events`: VC入退室ログ\n"
                "- `member-events`: 参加/退出ログ"
            ),
            inline=False,
        )
        embed.add_field(
            name="スラッシュコマンド",
            value=(
                "- `/help`: このヘルプ\n"
                "- `/bot_info`: Bot状態/疎通を表示\n"
                "- `/summarize_recent`: 直近メッセージを要約\n"
                "- `/set_recent_window`: 要約の既定件数を設定\n"
                "- `/config_show`: 設定値を表示\n"
                "- `/config_set`: 設定値を更新\n"
                "- `/minutes_start`: 議事録開始（VC参加者のみ）\n"
                "- `/minutes_stop`: 議事録停止して要約\n"
                "- `/minutes_status`: 議事録状態表示\n"
                "- `/reaction_role_set`: リアクションロール登録\n"
                "- `/reaction_role_remove`: リアクションロール解除\n"
                "- `/reaction_role_list`: リアクションロール一覧\n"
                "- `/tts_join`: 現在いる通話に入り、このチャンネルをVOICEVOX読み上げ対象に設定\n"
                "- `/tts_leave`: VOICEVOX読み上げ停止\n"
                "- `/tts_voice`: 読み上げ話者ID変更\n"
                "- `/tts_status`: 読み上げ状態表示\n"
                "- `/game`: ミニゲーム（人狼DM襲撃/あいうえおバトル等）\n"
                "- `/timer`: タイマー開始\n"
                "- `/vc_control`: VC参加者のミュート/スピーカーミュート制御"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _git_short_commit(self) -> str:
        build_info = load_build_info()
        build_commit = build_info.get("commit")
        if build_commit:
            return build_commit
        try:
            cp = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            out = (cp.stdout or "").strip()
            return out or "unknown"
        except Exception:
            return "unknown"

    def _git_version(self) -> str:
        build_info = load_build_info()
        build_version = build_info.get("version")
        if build_version:
            return build_version
        commit = self._git_short_commit()
        try:
            cp = subprocess.run(
                ["git", "status", "--porcelain"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            dirty = bool((cp.stdout or "").strip())
            return f"{commit}-dirty" if dirty else commit
        except Exception:
            return commit

    @app_commands.command(name="vc_control", description="VCミュート操作パネルを作成")
    @app_commands.checks.cooldown(1, 15.0)
    async def vc_control(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        actor = interaction.user
        if not actor.guild_permissions.move_members:
            await interaction.response.send_message(
                "この操作には『通話メンバーの移動』権限が必要です。",
                ephemeral=True,
            )
            return

        voice = actor.voice
        if not voice or not isinstance(voice.channel, discord.VoiceChannel):
            await interaction.response.send_message("VCに参加してから実行してください。", ephemeral=True)
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("テキストチャンネルで実行してください。", ephemeral=True)
            return

        panel_text = (
            f"🎛️ **VCコントロールパネル**（対象VC: {voice.channel.name}）\n"
            f"{self.VC_JOIN_EMOJI} 参加登録\n"
            f"{self.VC_MUTE_ON_EMOJI} ミュートON / {self.VC_MUTE_OFF_EMOJI} ミュートOFF\n"
            f"{self.VC_DEAF_ON_EMOJI} スピーカーミュートON / {self.VC_DEAF_OFF_EMOJI} スピーカーミュートOFF\n"
            "※ 参加登録済み かつ VC参加中の人だけ操作できます。"
        )
        panel_msg = await interaction.channel.send(panel_text)
        for e in (
            self.VC_JOIN_EMOJI,
            self.VC_MUTE_ON_EMOJI,
            self.VC_MUTE_OFF_EMOJI,
            self.VC_DEAF_ON_EMOJI,
            self.VC_DEAF_OFF_EMOJI,
        ):
            try:
                await panel_msg.add_reaction(e)
            except Exception:
                pass

        self._vc_panels[panel_msg.id] = VcPanelState(
            guild_id=interaction.guild.id,
            channel_id=interaction.channel.id,
            voice_channel_id=voice.channel.id,
            host_user_id=interaction.user.id,
            joined_user_ids=set(),
        )
        await interaction.response.send_message("VCコントロールパネルを作成しました。", ephemeral=True)

    @app_commands.command(name="bot_info", description="Bot状態と疎通確認を表示")
    async def slash_bot_info(self, interaction: discord.Interaction):
        now = discord.utils.utcnow()
        uptime = now - self._started_at
        total_seconds = int(max(0, uptime.total_seconds()))
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        s = total_seconds % 60

        guild_count = len(self.bot.guilds)
        member_count = 0
        for g in self.bot.guilds:
            if g.member_count:
                member_count += int(g.member_count)

        ping_ms = round(self.bot.latency * 1000, 1)
        commit = self._git_short_commit()
        version = self._git_version()

        embed = discord.Embed(
            title="Kenny Bot 情報",
            color=discord.Color.green(),
            timestamp=datetime.now(JST),
        )
        embed.add_field(name="疎通", value="🏓 Pong / 正常", inline=True)
        embed.add_field(name="Ping", value=f"{ping_ms} ms", inline=True)
        embed.add_field(name="稼働時間", value=f"{h}h {m}m {s}s", inline=True)
        embed.add_field(name="参加サーバー", value=str(guild_count), inline=True)
        embed.add_field(name="総メンバー数(概算)", value=str(member_count), inline=True)
        embed.add_field(name="Version", value=f"`{version}`", inline=True)
        embed.add_field(name="Commit", value=f"`{commit}`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="summarize_recent", description="指定チャンネルの直近メッセージをAI要約")
    @app_commands.checks.cooldown(1, 20.0)
    @app_commands.describe(
        messages="何件を要約するか（1〜設定上限、省略時は設定値）",
        channel="省略時はこのチャンネル",
    )
    async def summarize_recent(
        self,
        interaction: discord.Interaction,
        messages: app_commands.Range[int, 1, 300] | None = None,
        channel: ReadableChannel | None = None,
    ):
        target = channel or interaction.channel
        if not self._is_readable_channel(target):
            await interaction.response.send_message("このチャンネルでは要約できません。", ephemeral=True)
            return

        guild_id = interaction.guild.id if interaction.guild else 0
        default_recent = int(
            _settings.get(
                "summarize_recent_default_messages",
                _settings.get("summarize_recent_default_minutes", 30, guild_id=guild_id),
                guild_id=guild_id,
            )
        )
        max_messages = int(
            _settings.get(
                "summarize_recent.max_messages",
                _settings.get("summarize_recent.max_minutes", 300, guild_id=guild_id),
                guild_id=guild_id,
            )
        )
        fetch_limit = int(_settings.get("summarize_recent.history_fetch_limit", 300, guild_id=guild_id))
        line_limit = int(_settings.get("summarize_recent.transcript_lines_limit", 120, guild_id=guild_id))
        messages_val = int(messages) if messages is not None else default_recent
        if messages_val < 1:
            messages_val = 1
        if max_messages > 0 and messages_val > max_messages:
            messages_val = max_messages

        await interaction.response.defer(ephemeral=True, thinking=True)

        rows: List[str] = []
        history_limit = max(50, fetch_limit, messages_val * 2)
        async for m in target.history(limit=history_limit):
            if m.author.bot:
                continue
            text = (m.content or "").strip()
            if not text:
                continue
            name = m.author.display_name if isinstance(m.author, discord.Member) else m.author.name
            rows.append(f"[{m.created_at.astimezone(JST).strftime('%H:%M')}] {name}: {text[:160]}")
            if len(rows) >= messages_val:
                break

        if not rows:
            await interaction.followup.send(
                f"要約対象メッセージが見つかりませんでした。（指定: {messages_val}件）",
                ephemeral=True,
            )
            return

        rows = list(reversed(rows[:messages_val]))
        transcript = "\n".join(rows[:max(20, line_limit)])
        prompt = (
            "以下は Discord のチャットログです。\n"
            "日本語で簡潔に要約してください。\n"
            "出力形式:\n"
            "1) 全体の要点（3行以内）\n"
            "2) 話題トピック（箇条書き最大5件）\n"
            "3) 次アクション/未解決事項（あれば）\n\n"
            f"対象チャンネル: #{target.name}\n"
            f"対象件数: 直近{len(rows)}件\n\n"
            f"{transcript}"
        )

        try:
            model_summary = str(_settings.get("ollama.model_summary", "gpt-oss:120b"))
            summary = self.bot.ollama_client.chat_simple(
                model=model_summary,
                prompt=prompt,
                stream=False,
            )
            summary = (summary or "").strip() or "要約結果が空でした。"
        except Exception as e:
            await interaction.followup.send(
                f"要約に失敗しました: {str(e)[:180]}",
                ephemeral=True,
            )
            return

        if len(summary) > 1800:
            summary = summary[:1800] + "\n...(省略)..."

        embed = discord.Embed(
            title=f"直近{len(rows)}件のチャット要約",
            description=summary,
            color=discord.Color.orange(),
            timestamp=datetime.now(JST),
        )
        embed.set_footer(text=f"#{target.name} / 対象メッセージ数: {len(rows)}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="set_recent_window", description="チャット要約の既定件数を設定")
    @app_commands.describe(messages="既定の件数（1〜300）")
    async def set_recent_window(
        self,
        interaction: discord.Interaction,
        messages: app_commands.Range[int, 1, 300],
    ):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        _settings.set("summarize_recent_default_messages", int(messages), guild_id=interaction.guild.id)
        await interaction.response.send_message(
            f"要約の既定件数を **{int(messages)}件** に設定しました。\n"
            "次回 `/summarize_recent` で messages 省略時に適用されます。",
            ephemeral=True,
        )

    @app_commands.command(name="config_show", description="設定値を表示")
    @app_commands.describe(key="表示する設定キー")
    @app_commands.choices(key=_CONFIG_CHOICES)
    @checks.has_permissions(administrator=True)
    async def config_show(self, interaction: discord.Interaction, key: app_commands.Choice[str]):
        gid = interaction.guild.id if interaction.guild else None
        value = _settings.get(key.value, None, guild_id=gid)
        await interaction.response.send_message(
            f"`{key.value}` = `{value}`",
            ephemeral=True,
        )

    @app_commands.command(name="config_set", description="設定値を更新")
    @app_commands.describe(
        key="更新する設定キー",
        value="新しい値（数値キーは数字、モデルは文字列）",
        scope="global:全体 / guild:このサーバーのみ",
    )
    @app_commands.choices(
        key=_CONFIG_CHOICES,
        scope=[
            app_commands.Choice(name="global", value="global"),
            app_commands.Choice(name="guild", value="guild"),
        ],
    )
    @checks.has_permissions(administrator=True)
    async def config_set(
        self,
        interaction: discord.Interaction,
        key: app_commands.Choice[str],
        value: str,
        scope: app_commands.Choice[str] | None = None,
    ):
        sc = scope.value if scope else "global"
        guild_id = interaction.guild.id if (sc == "guild" and interaction.guild) else None
        if sc == "guild" and guild_id is None:
            await interaction.response.send_message("guild スコープはサーバー内で実行してください。", ephemeral=True)
            return
        if sc == "global":
            if not interaction.guild or interaction.user.id != interaction.guild.owner_id:
                await interaction.response.send_message(
                    "global スコープはサーバーオーナーのみ変更できます。",
                    ephemeral=True,
                )
                return

        parsed: object
        if key.value in self._INT_KEYS:
            try:
                parsed = int(value)
            except Exception:
                await interaction.response.send_message("このキーは整数で指定してください。", ephemeral=True)
                return
        elif key.value in self._BOOL_KEYS:
            v = value.strip().lower()
            if v in {"1", "true", "on", "yes", "有効"}:
                parsed = True
            elif v in {"0", "false", "off", "no", "無効"}:
                parsed = False
            else:
                await interaction.response.send_message("このキーは true/false で指定してください。", ephemeral=True)
                return
        else:
            parsed = value.strip()
            if not parsed:
                await interaction.response.send_message("空文字は設定できません。", ephemeral=True)
                return

        _settings.set(key.value, parsed, guild_id=guild_id)
        note = "（一部設定は再起動後に完全反映）"
        await interaction.response.send_message(
            f"設定を更新しました: `{key.value}` = `{parsed}` / scope=`{sc}` {note}",
            ephemeral=True,
        )

    @app_commands.command(name="reaction_role_set", description="メッセージのリアクションにロール付与を紐付け")
    @checks.has_permissions(administrator=True)
    @app_commands.describe(
        message_id="対象メッセージID",
        emoji="対象リアクション",
        role="付与するロール",
    )
    async def reaction_role_set(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
        role: discord.Role,
    ):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        try:
            msg_id = str(int(message_id.strip()))
        except Exception:
            await interaction.response.send_message("message_id は数値で指定してください。", ephemeral=True)
            return

        emoji_key = emoji.strip()
        if not emoji_key:
            await interaction.response.send_message("emoji を指定してください。", ephemeral=True)
            return

        me = interaction.guild.me
        if me is None or not me.guild_permissions.manage_roles:
            await interaction.response.send_message("Botに『ロールの管理』権限がありません。", ephemeral=True)
            return
        if role >= me.top_role:
            await interaction.response.send_message(
                "そのロールはBotの最上位ロール以上なので付与できません。",
                ephemeral=True,
            )
            return

        bindings = _settings.get("reaction_roles.bindings", {}, guild_id=interaction.guild.id)
        if not isinstance(bindings, dict):
            bindings = {}
        per_message = bindings.get(msg_id, {})
        if not isinstance(per_message, dict):
            per_message = {}
        per_message[emoji_key] = int(role.id)
        bindings[msg_id] = per_message
        _settings.set("reaction_roles.bindings", bindings, guild_id=interaction.guild.id)
        await interaction.response.send_message(
            f"登録しました: message_id=`{msg_id}` / emoji=`{emoji_key}` / role={role.mention}\n"
            "このリアクションを押した管理者本人にロールを付与します。",
            ephemeral=True,
        )

    @app_commands.command(name="reaction_role_remove", description="メッセージのリアクションロール設定を解除")
    @checks.has_permissions(administrator=True)
    @app_commands.describe(
        message_id="対象メッセージID",
        emoji="解除するリアクション",
    )
    async def reaction_role_remove(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
    ):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return
        try:
            msg_id = str(int(message_id.strip()))
        except Exception:
            await interaction.response.send_message("message_id は数値で指定してください。", ephemeral=True)
            return

        emoji_key = emoji.strip()
        bindings = _settings.get("reaction_roles.bindings", {}, guild_id=interaction.guild.id)
        if not isinstance(bindings, dict):
            bindings = {}
        per_message = bindings.get(msg_id, {})
        if not isinstance(per_message, dict) or emoji_key not in per_message:
            await interaction.response.send_message("対象設定が見つかりません。", ephemeral=True)
            return

        per_message.pop(emoji_key, None)
        if per_message:
            bindings[msg_id] = per_message
        else:
            bindings.pop(msg_id, None)
        _settings.set("reaction_roles.bindings", bindings, guild_id=interaction.guild.id)
        await interaction.response.send_message(
            f"解除しました: message_id=`{msg_id}` / emoji=`{emoji_key}`",
            ephemeral=True,
        )

    @app_commands.command(name="reaction_role_list", description="リアクションロール設定を一覧表示")
    @checks.has_permissions(administrator=True)
    async def reaction_role_list(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        bindings = _settings.get("reaction_roles.bindings", {}, guild_id=interaction.guild.id)
        if not isinstance(bindings, dict) or not bindings:
            await interaction.response.send_message("リアクションロール設定はありません。", ephemeral=True)
            return

        lines: List[str] = []
        for msg_id, per_message in bindings.items():
            if not isinstance(per_message, dict):
                continue
            for emoji_key, role_id in per_message.items():
                role = interaction.guild.get_role(int(role_id))
                role_text = role.mention if role else f"`{role_id}`"
                lines.append(f"message_id=`{msg_id}` / emoji=`{emoji_key}` / role={role_text}")

        if not lines:
            await interaction.response.send_message("リアクションロール設定はありません。", ephemeral=True)
            return

        await interaction.response.send_message("\n".join(lines[:30]), ephemeral=True)

    @app_commands.command(name="minutes_start", description="議事録モードを開始（VC参加者のみ）")
    @app_commands.checks.cooldown(1, 10.0)
    async def minutes_start(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        voice = interaction.user.voice
        if not voice or not isinstance(voice.channel, (discord.VoiceChannel, discord.StageChannel)):
            await interaction.response.send_message("VCに参加してから実行してください。", ephemeral=True)
            return
        voice_channel = voice.channel
        voice_channel_name = voice_channel.name

        existing_vc = interaction.guild.voice_client
        if existing_vc and existing_vc.is_connected():
            current = getattr(existing_vc, "channel", None)
            current_name = current.name if isinstance(current, (discord.VoiceChannel, discord.StageChannel)) else "不明"
            await interaction.response.send_message(
                f"Bot はすでに VC `{current_name}` に接続中です。"
                " 先に `/tts_leave` または議事録停止を実行してから再試行してください。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        ok, msg = await self.bot.meeting_minutes.start_session(
            bot=self.bot,
            guild=interaction.guild,
            voice_channel=voice_channel,
            started_by_id=interaction.user.id,
            announce_channel_id=interaction.channel_id,
        )
        await interaction.followup.send(msg, ephemeral=True)
        if ok:
            out = self.bot.meeting_minutes.resolve_announce_channel(
                interaction.guild,
                interaction.channel_id,
                allow_fallback=False,
            )
            if out:
                await out.send(f"{interaction.user.mention} 議事録を開始しました。（VC: {voice_channel_name}）")

    @app_commands.command(name="minutes_stop", description="議事録モードを停止して要約を作成")
    @app_commands.checks.cooldown(1, 15.0)
    async def minutes_stop(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self.bot.meeting_minutes.stop_session(
            bot=self.bot,
            guild=interaction.guild,
            reason=f"{interaction.user.display_name} が手動停止",
            mention_user_id=interaction.user.id,
        )
        if not result:
            await interaction.followup.send("現在、進行中の議事録はありません。", ephemeral=True)
            return

        embed = self.bot.meeting_minutes.build_result_embed(interaction.guild, result)
        await interaction.followup.send("議事録を停止し、要約を作成しました。", ephemeral=True)

        out_ch = self.bot.meeting_minutes.resolve_announce_channel(
            interaction.guild,
            interaction.channel_id,
            allow_fallback=False,
        )
        if out_ch:
            await out_ch.send(content=f"<@{result.mention_user_id}>", embed=embed)

    @app_commands.command(name="minutes_status", description="議事録モードの状態を表示")
    async def minutes_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("サーバー内で実行してください。", ephemeral=True)
            return

        session = self.bot.meeting_minutes.get_session(interaction.guild.id)
        if not session:
            await interaction.response.send_message("議事録は停止中です。", ephemeral=True)
            return

        vc = interaction.guild.get_channel(session.voice_channel_id)
        started = session.started_at.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")
        vc_name = vc.name if isinstance(vc, (discord.VoiceChannel, discord.StageChannel)) else f"ID:{session.voice_channel_id}"
        await interaction.response.send_message(
            f"議事録は進行中です。\nVC: {vc_name}\n開始: {started}",
            ephemeral=True,
        )

    @app_commands.command(name="timer", description="タイマーを開始（時/分/秒指定）")
    @app_commands.checks.cooldown(2, 10.0)
    @app_commands.describe(
        hours="時間（0〜23）",
        minutes="分（0〜59）",
        seconds="秒（0〜59）",
        title="終了時に表示するメッセージ（任意）",
    )
    async def timer(
        self,
        interaction: discord.Interaction,
        hours: app_commands.Range[int, 0, 23] = 0,
        minutes: app_commands.Range[int, 0, 59] = 0,
        seconds: app_commands.Range[int, 0, 59] = 0,
        title: str | None = None,
    ):
        total_seconds = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
        if total_seconds <= 0:
            await interaction.response.send_message(
                "時間を指定してください（例: 0時間 1分 30秒）。",
                ephemeral=True,
            )
            return
        if total_seconds > 24 * 3600:
            await interaction.response.send_message(
                "最大24時間までにしてください。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"⏱️ タイマー開始: {hours}時間 {minutes}分 {seconds}秒",
            ephemeral=True,
        )

        if isinstance(interaction.channel, discord.TextChannel):
            await self._run_timer_countdown(
                channel=interaction.channel,
                mention_user_id=interaction.user.id,
                total_seconds=total_seconds,
                title=title,
            )
            return

        await discord.utils.sleep_until(discord.utils.utcnow() + timedelta(seconds=total_seconds))
        done_text = title.strip() if title and title.strip() else "タイマー終了です。"
        try:
            await interaction.user.send(f"⏰ {done_text}")
        except Exception:
            pass

    async def _run_timer_countdown(
        self,
        channel: discord.TextChannel,
        mention_user_id: int,
        total_seconds: int,
        title: str | None,
    ) -> None:
        countdown_msg = await channel.send(
            f"<@{mention_user_id}> ⏳ 残り {total_seconds} 秒"
        )
        remain = total_seconds
        while remain > 0:
            # 長時間は10秒ごと、最後の10秒だけ1秒ごとに更新
            step = 1 if remain <= 10 else 10
            await asyncio.sleep(step)
            remain -= step
            if remain > 0:
                await countdown_msg.edit(
                    content=f"<@{mention_user_id}> ⏳ 残り {remain} 秒"
                )
            else:
                done_text = title.strip() if title and title.strip() else "タイマー終了です。"
                await countdown_msg.edit(
                    content=f"<@{mention_user_id}> ⏰ {done_text}\n🔁 を押すと同じ設定で再スタート"
                )
                try:
                    await countdown_msg.add_reaction("🔁")
                except Exception:
                    pass
                self._timer_restart_templates[countdown_msg.id] = (int(total_seconds), done_text)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == (self.bot.user.id if self.bot.user else 0):
            return
        emoji = str(payload.emoji)

        # タイマー再スタート
        if emoji == "🔁":
            tpl = self._timer_restart_templates.get(payload.message_id)
            if not tpl:
                return
            channel = self.bot.get_channel(payload.channel_id)
            if not isinstance(channel, discord.TextChannel):
                return
            seconds, done_text = tpl
            asyncio.create_task(
                self._run_timer_countdown(
                    channel=channel,
                    mention_user_id=payload.user_id,
                    total_seconds=int(seconds),
                    title=done_text,
                )
            )
            return

        panel = self._vc_panels.get(payload.message_id)
        if not panel:
            return
        if emoji not in {
            self.VC_JOIN_EMOJI,
            self.VC_MUTE_ON_EMOJI,
            self.VC_MUTE_OFF_EMOJI,
            self.VC_DEAF_ON_EMOJI,
            self.VC_DEAF_OFF_EMOJI,
        }:
            return

        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        if not guild or guild.id != panel.guild_id:
            return
        member = guild.get_member(payload.user_id)
        if not isinstance(member, discord.Member):
            return
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        # 参加登録
        if emoji == self.VC_JOIN_EMOJI:
            if not member.voice or not isinstance(member.voice.channel, discord.VoiceChannel):
                await channel.send(f"{member.mention} VC参加中のみ登録できます。", delete_after=5)
                return
            if member.voice.channel.id != panel.voice_channel_id:
                await channel.send(f"{member.mention} 対象VCに参加してから登録してください。", delete_after=5)
                return
            panel.joined_user_ids.add(member.id)
            await channel.send(f"{member.mention} を参加登録しました。", delete_after=5)
            return

        # 操作側の条件: 参加登録済み + 対象VCに接続中 + move_members 権限
        if member.id not in panel.joined_user_ids:
            await channel.send(f"{member.mention} 先に {self.VC_JOIN_EMOJI} で参加登録してください。", delete_after=5)
            return
        if not member.voice or not isinstance(member.voice.channel, discord.VoiceChannel) or member.voice.channel.id != panel.voice_channel_id:
            await channel.send(f"{member.mention} 対象VCに参加中のときのみ操作できます。", delete_after=5)
            return
        if not member.guild_permissions.move_members:
            await channel.send(f"{member.mention} この操作には『通話メンバーの移動』権限が必要です。", delete_after=5)
            return

        me = guild.me
        if me is None:
            return
        targets = []
        vc = guild.get_channel(panel.voice_channel_id)
        if isinstance(vc, discord.VoiceChannel):
            for uid in panel.joined_user_ids:
                tm = guild.get_member(uid)
                if isinstance(tm, discord.Member) and tm.voice and tm.voice.channel and tm.voice.channel.id == vc.id and not tm.bot:
                    targets.append(tm)

        op = None
        if emoji == self.VC_MUTE_ON_EMOJI:
            op = "mute_on"
            if not me.guild_permissions.mute_members:
                await channel.send("Botに『メンバーをミュート』権限がありません。", delete_after=5)
                return
        elif emoji == self.VC_MUTE_OFF_EMOJI:
            op = "mute_off"
            if not me.guild_permissions.mute_members:
                await channel.send("Botに『メンバーをミュート』権限がありません。", delete_after=5)
                return
        elif emoji == self.VC_DEAF_ON_EMOJI:
            op = "deafen_on"
            if not me.guild_permissions.deafen_members:
                await channel.send("Botに『メンバーをスピーカーミュート』権限がありません。", delete_after=5)
                return
        elif emoji == self.VC_DEAF_OFF_EMOJI:
            op = "deafen_off"
            if not me.guild_permissions.deafen_members:
                await channel.send("Botに『メンバーをスピーカーミュート』権限がありません。", delete_after=5)
                return
        if op is None:
            return

        success = 0
        failed = 0
        for tm in targets:
            if tm.id == me.id or tm.top_role >= me.top_role:
                failed += 1
                continue
            try:
                if op == "mute_on":
                    await tm.edit(mute=True, reason=f"{member} によるVCパネル操作")
                elif op == "mute_off":
                    await tm.edit(mute=False, reason=f"{member} によるVCパネル操作")
                elif op == "deafen_on":
                    await tm.edit(deafen=True, reason=f"{member} によるVCパネル操作")
                elif op == "deafen_off":
                    await tm.edit(deafen=False, reason=f"{member} によるVCパネル操作")
                success += 1
            except Exception:
                failed += 1

        await channel.send(
            f"{member.mention} 操作を実行しました。成功 {success} / 失敗 {failed}",
            delete_after=7,
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            text = f"連続実行を制限中です。{error.retry_after:.1f}秒後に再試行してください。"
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
            return
        raise error
