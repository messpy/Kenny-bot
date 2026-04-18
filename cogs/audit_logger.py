from __future__ import annotations

from typing import Iterable

import discord
from discord.ext import commands

from utils.event_logger import send_event_log


def _fmt_channel(channel: discord.abc.GuildChannel | None) -> str:
    if channel is None:
        return "None"
    return f"{channel.name} ({channel.id})"


def _truncate(value: str, limit: int = 1000) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


class AuditLogger(commands.Cog):
    """監査向けイベントログ"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if not payload.guild_id or payload.user_id == (self.bot.user.id if self.bot.user else 0):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        member = guild.get_member(payload.user_id)
        user_label = f"{member} ({member.id})" if member else str(payload.user_id)
        await send_event_log(
            self.bot,
            guild=guild,
            title="リアクション追加",
            description=f"{user_label} がリアクションを追加しました。",
            fields=[
                ("絵文字", str(payload.emoji), True),
                ("メッセージID", str(payload.message_id), True),
                ("チャンネルID", str(payload.channel_id), True),
            ],
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if not payload.guild_id or payload.user_id == (self.bot.user.id if self.bot.user else 0):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        await send_event_log(
            self.bot,
            guild=guild,
            title="リアクション削除",
            description=f"{payload.user_id} がリアクションを削除しました。",
            fields=[
                ("絵文字", str(payload.emoji), True),
                ("メッセージID", str(payload.message_id), True),
                ("チャンネルID", str(payload.channel_id), True),
            ],
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.application_command:
            return
        guild = interaction.guild
        data = interaction.data if isinstance(interaction.data, dict) else {}
        name = str(data.get("name") or "unknown")
        options = data.get("options") or []
        option_text = _truncate(str(options), 1000)
        user = interaction.user
        await send_event_log(
            self.bot,
            guild=guild,
            title="Slash Command 実行",
            description=f"/{name} が実行されました。",
            fields=[
                ("ユーザー", f"{user} ({user.id})", False),
                ("チャンネル", _fmt_channel(interaction.channel) if isinstance(interaction.channel, discord.abc.GuildChannel) else str(getattr(interaction.channel, 'id', 'DM')), False),
                ("オプション", option_text or "[]", False),
            ],
        )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        await send_event_log(
            self.bot,
            guild=role.guild,
            title="ロール作成",
            description=f"ロール `{role.name}` が作成されました。",
            fields=[("ロール", f"{role.name} ({role.id})", False)],
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await send_event_log(
            self.bot,
            guild=role.guild,
            level="warning",
            title="ロール削除",
            description=f"ロール `{role.name}` が削除されました。",
            fields=[("ロール", f"{role.name} ({role.id})", False)],
        )

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes: list[tuple[str, str, bool]] = []
        if before.name != after.name:
            changes.append(("名前", f"{before.name} -> {after.name}", False))
        if before.permissions.value != after.permissions.value:
            changes.append(("権限", f"{before.permissions.value} -> {after.permissions.value}", False))
        if before.color.value != after.color.value:
            changes.append(("色", f"{before.color} -> {after.color}", True))
        if before.hoist != after.hoist:
            changes.append(("hoist", f"{before.hoist} -> {after.hoist}", True))
        if before.mentionable != after.mentionable:
            changes.append(("mentionable", f"{before.mentionable} -> {after.mentionable}", True))
        if not changes:
            return
        await send_event_log(
            self.bot,
            guild=after.guild,
            title="ロール更新",
            description=f"ロール `{after.name}` が更新されました。",
            fields=[("ロール", f"{after.name} ({after.id})", False), *changes[:8]],
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await send_event_log(
            self.bot,
            guild=channel.guild,
            title="チャンネル作成",
            description=f"チャンネル `{channel.name}` が作成されました。",
            fields=[("チャンネル", f"{channel.name} ({channel.id})", False)],
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await send_event_log(
            self.bot,
            guild=channel.guild,
            level="warning",
            title="チャンネル削除",
            description=f"チャンネル `{channel.name}` が削除されました。",
            fields=[("チャンネル", f"{channel.name} ({channel.id})", False)],
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self,
        before: discord.abc.GuildChannel,
        after: discord.abc.GuildChannel,
    ) -> None:
        changes: list[tuple[str, str, bool]] = []
        if before.name != after.name:
            changes.append(("名前", f"{before.name} -> {after.name}", False))
        if getattr(before, "category_id", None) != getattr(after, "category_id", None):
            changes.append(("カテゴリ", f"{getattr(before, 'category_id', None)} -> {getattr(after, 'category_id', None)}", False))
        if getattr(before, "position", None) != getattr(after, "position", None):
            changes.append(("位置", f"{getattr(before, 'position', None)} -> {getattr(after, 'position', None)}", True))
        if not changes:
            return
        await send_event_log(
            self.bot,
            guild=after.guild,
            title="チャンネル更新",
            description=f"チャンネル `{after.name}` が更新されました。",
            fields=[("チャンネル", f"{after.name} ({after.id})", False), *changes[:8]],
        )

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        changes: list[tuple[str, str, bool]] = []
        if before.name != after.name:
            changes.append(("名前", f"{before.name} -> {after.name}", False))
        if before.description != after.description:
            changes.append(("説明", f"{before.description or 'None'} -> {after.description or 'None'}", False))
        if before.verification_level != after.verification_level:
            changes.append(("認証レベル", f"{before.verification_level} -> {after.verification_level}", True))
        if before.system_channel != after.system_channel:
            changes.append(("system_channel", f"{_fmt_channel(before.system_channel)} -> {_fmt_channel(after.system_channel)}", False))
        if before.afk_channel != after.afk_channel:
            changes.append(("afk_channel", f"{_fmt_channel(before.afk_channel)} -> {_fmt_channel(after.afk_channel)}", False))
        if not changes:
            return
        await send_event_log(
            self.bot,
            guild=after,
            title="サーバー設定更新",
            description=f"サーバー `{after.name}` の設定が更新されました。",
            fields=changes[:8],
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member) -> None:
        await send_event_log(
            self.bot,
            guild=guild,
            level="warning",
            title="メンバーBAN",
            description="メンバーが BAN されました。",
            fields=[
                ("ユーザー", f"{user} ({user.id})", False),
                ("サーバー", f"{guild.name} ({guild.id})", False),
            ],
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        await send_event_log(
            self.bot,
            guild=guild,
            level="info",
            title="メンバーBAN解除",
            description="メンバーの BAN が解除されました。",
            fields=[
                ("ユーザー", f"{user} ({user.id})", False),
                ("サーバー", f"{guild.name} ({guild.id})", False),
            ],
        )
