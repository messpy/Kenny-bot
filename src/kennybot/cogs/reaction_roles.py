# cogs/reaction_roles.py
# リアクションロール付与

import logging

import discord
from discord.ext import commands

from src.kennybot.utils.event_logger import send_event_log
from src.kennybot.utils.runtime_settings import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()


class ReactionRoles(commands.Cog):
    """対象メッセージのリアクションを押した人にロールを付与"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _fetch_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if not payload.guild_id or payload.user_id == (self.bot.user.id if self.bot.user else 0):
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = await self._fetch_member(guild, payload.user_id)
        if not isinstance(member, discord.Member) or member.bot:
            return

        bindings = _settings.get("reaction_roles.bindings", {}, guild_id=guild.id)
        if not isinstance(bindings, dict):
            return

        per_message = bindings.get(str(payload.message_id), {})
        if not isinstance(per_message, dict):
            return

        role_id = per_message.get(str(payload.emoji))
        if not role_id:
            return

        role = guild.get_role(int(role_id))
        me = guild.me
        if not isinstance(role, discord.Role) or me is None:
            return
        if role in member.roles:
            return
        if not me.guild_permissions.manage_roles:
            logger.warning("Missing manage_roles permission for reaction role assignment")
            await send_event_log(
                self.bot,
                guild=guild,
                level="warning",
                title="リアクションロール失敗",
                description="ロール付与権限が不足しているため処理できませんでした。",
                fields=[
                    ("ユーザー", f"{member} ({member.id})", False),
                    ("ロール", f"{role.name} ({role.id})", False),
                    ("絵文字", str(payload.emoji), True),
                ],
            )
            return
        if role >= me.top_role:
            logger.warning("Cannot assign role %s due to hierarchy", role.id)
            await send_event_log(
                self.bot,
                guild=guild,
                level="warning",
                title="リアクションロール失敗",
                description="Bot のロール階層が不足しているため処理できませんでした。",
                fields=[
                    ("ユーザー", f"{member} ({member.id})", False),
                    ("ロール", f"{role.name} ({role.id})", False),
                    ("絵文字", str(payload.emoji), True),
                ],
            )
            return

        try:
            await member.add_roles(role, reason=f"Reaction role via emoji {payload.emoji}")
            await send_event_log(
                self.bot,
                guild=guild,
                level="success",
                title="リアクションロール付与",
                description=f"{member.mention} にロールを付与しました。",
                fields=[
                    ("ユーザー", f"{member} ({member.id})", False),
                    ("ロール", f"{role.name} ({role.id})", False),
                    ("絵文字", str(payload.emoji), True),
                    ("メッセージID", str(payload.message_id), True),
                ],
            )
        except Exception:
            logger.exception("Failed to add role %s to member %s", role.id, member.id)
            await send_event_log(
                self.bot,
                guild=guild,
                level="error",
                title="リアクションロール失敗",
                description="リアクションロール付与中に例外が発生しました。",
                fields=[
                    ("ユーザー", f"{member} ({member.id})", False),
                    ("ロール", f"{role.name} ({role.id})", False),
                    ("絵文字", str(payload.emoji), True),
                    ("メッセージID", str(payload.message_id), True),
                ],
            )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if not payload.guild_id or payload.user_id == (self.bot.user.id if self.bot.user else 0):
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = await self._fetch_member(guild, payload.user_id)
        if not isinstance(member, discord.Member) or member.bot:
            return

        bindings = _settings.get("reaction_roles.bindings", {}, guild_id=guild.id)
        if not isinstance(bindings, dict):
            return

        per_message = bindings.get(str(payload.message_id), {})
        if not isinstance(per_message, dict):
            return

        role_id = per_message.get(str(payload.emoji))
        if not role_id:
            return

        role = guild.get_role(int(role_id))
        me = guild.me
        if not isinstance(role, discord.Role) or me is None:
            return
        if role not in member.roles:
            return
        if not me.guild_permissions.manage_roles:
            logger.warning("Missing manage_roles permission for reaction role removal")
            return
        if role >= me.top_role:
            logger.warning("Cannot remove role %s due to hierarchy", role.id)
            return

        try:
            await member.remove_roles(role, reason=f"Reaction role removed via emoji {payload.emoji}")
            await send_event_log(
                self.bot,
                guild=guild,
                level="success",
                title="リアクションロール解除",
                description=f"{member.mention} からロールを解除しました。",
                fields=[
                    ("ユーザー", f"{member} ({member.id})", False),
                    ("ロール", f"{role.name} ({role.id})", False),
                    ("絵文字", str(payload.emoji), True),
                    ("メッセージID", str(payload.message_id), True),
                ],
            )
        except Exception:
            logger.exception("Failed to remove role %s from member %s", role.id, member.id)
            await send_event_log(
                self.bot,
                guild=guild,
                level="error",
                title="リアクションロール解除失敗",
                description="リアクションロール解除中に例外が発生しました。",
                fields=[
                    ("ユーザー", f"{member} ({member.id})", False),
                    ("ロール", f"{role.name} ({role.id})", False),
                    ("絵文字", str(payload.emoji), True),
                    ("メッセージID", str(payload.message_id), True),
                ],
            )
