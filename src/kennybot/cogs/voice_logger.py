# cogs/voice_logger.py
# VC ログ（Embed 形式 / 通話時間付き）

from datetime import datetime, timezone, timedelta
from typing import Dict, Tuple, Optional

import discord
from discord.ext import commands
from src.kennybot.utils.runtime_settings import get_settings
from src.kennybot.utils.event_logger import send_event_log

JST = timezone(timedelta(hours=9))
_settings = get_settings()


class VoiceLogger(commands.Cog):
    """VC 入退室ログ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # ユーザーごとの入室時刻を記録: (user_id, guild_id) -> datetime
        self._voice_join_times: Dict[Tuple[int, int], datetime] = {}

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        """VC の参加・離脱・移動を監視"""
        guild = member.guild

        # 入室した場合
        if before.channel is None and after.channel is not None:
            await self._handle_voice_join(member, after.channel, guild)

        # 退出した場合
        elif before.channel is not None and after.channel is None:
            await self._handle_voice_leave(member, before.channel, guild)

        # チャンネル変更した場合（退出+入室として扱う）
        elif before.channel != after.channel:
            await self._handle_voice_leave(member, before.channel, guild)
            await self._handle_voice_join(member, after.channel, guild)

        # 議事録モード中のVCが無人になったら自動停止
        await self._maybe_auto_stop_minutes(guild, before.channel, after.channel)

    def _should_log_channel(self, guild: discord.Guild, channel: discord.VoiceChannel) -> bool:
        if bool(_settings.get("voice.log_private_channels", False, guild_id=guild.id)):
            return True

        everyone = guild.default_role
        perms = channel.permissions_for(everyone)
        return bool(perms.view_channel and perms.connect)

    async def _maybe_auto_stop_minutes(
        self,
        guild: discord.Guild,
        before_channel: Optional[discord.VoiceChannel],
        after_channel: Optional[discord.VoiceChannel],
    ):
        session = self.bot.meeting_minutes.get_session(guild.id)  # type: ignore[attr-defined]
        if not session:
            return

        target = guild.get_channel(session.voice_channel_id)
        if not isinstance(target, discord.VoiceChannel):
            return

        # 更新対象が議事録VCに関係ないなら無視
        if before_channel != target and after_channel != target:
            return

        if not self.bot.meeting_minutes.is_human_empty(target):  # type: ignore[attr-defined]
            # 参加者がいても最大時間を超えたら自動停止（負荷対策）
            max_minutes = int(_settings.get("meeting.max_minutes", 90))
            elapsed = datetime.now(timezone.utc) - session.started_at
            if max_minutes <= 0 or elapsed < timedelta(minutes=max_minutes):
                return
            reason = f"最大録音時間 {max_minutes} 分を超えたため自動停止"
        else:
            reason = "VCが無人になったため自動停止"

        result = await self.bot.meeting_minutes.stop_session(  # type: ignore[attr-defined]
            bot=self.bot,
            guild=guild,
            reason=reason,
            mention_user_id=session.started_by_id,
        )
        if not result:
            return

        await self.bot.meeting_minutes.deliver_stop_result(  # type: ignore[attr-defined]
            self.bot,
            guild,
            result,
            action="minutes_auto_stop",
            source_channel_id=session.announce_channel_id,
        )

    async def _handle_voice_join(self, member: discord.Member, channel: discord.VoiceChannel, guild: discord.Guild):
        """VC入室を記録してロギング"""
        # 入室時刻を記録
        self._voice_join_times[(member.id, guild.id)] = datetime.now(JST)

        if not self._should_log_channel(guild, channel):
            return

        await send_event_log(
            self.bot,
            guild=guild,
            level="success",
            title="VC入室",
            description=f"{member.mention} が {channel.name} に入室しました",
            fields=[
                ("ユーザー", f"{member.name} ({member.id})", False),
                ("サーバー", f"{guild.name} ({guild.id})", False),
                ("チャンネル", channel.name, False),
            ],
        )

    async def _handle_voice_leave(self, member: discord.Member, channel: discord.VoiceChannel, guild: discord.Guild):
        """VC離脱を記録してロギング"""
        # 入室時刻を取得
        join_time = self._voice_join_times.pop((member.id, guild.id), None)
        duration = self._calculate_duration(join_time) if join_time else "不明"

        if not self._should_log_channel(guild, channel):
            return

        await send_event_log(
            self.bot,
            guild=guild,
            level="warning",
            title="VC離脱",
            description=f"{member.mention} が {channel.name} から離脱しました",
            fields=[
                ("ユーザー", f"{member.name} ({member.id})", False),
                ("サーバー", f"{guild.name} ({guild.id})", False),
                ("チャンネル", channel.name, False),
                ("通話時間", duration, False),
            ],
        )

    def _calculate_duration(self, join_time: Optional[datetime]) -> str:
        """入室時刻から通話時間を計算"""
        if not join_time:
            return "不明"

        duration = datetime.now(JST) - join_time
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        return f"{hours}:{minutes:02d}:{seconds:02d}"
