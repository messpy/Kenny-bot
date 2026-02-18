# cogs/mod_panel.py
# スパム管理パネル（リアクション＆リセット機能）

import logging
from typing import Optional

import discord
from discord.ext import commands

from guards.spam_guard import SpamGuard
from utils.config import MOD_PANEL_CHANNEL_ID

logger = logging.getLogger(__name__)


class ModPanel(commands.Cog):
    """モデレーション管理パネル"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        """リアクション追加時のイベント"""
        # Bot のリアクションは無視
        if user.bot:
            return

        guild = reaction.message.guild
        if not guild:
            return

        # 🔄 リアクション（どのチャンネルでも対応）
        emoji = str(reaction.emoji)
        if emoji == "🔄":
            # スパムログ Embed か mod_panel メッセージか判定
            if self._is_spam_log(reaction.message) or self._is_mod_panel_message(reaction.message):
                await self._handle_reset(reaction, user, guild)
        # 📋 → 違反一覧を表示（mod_panel のみ）
        elif emoji == "📋":
            if reaction.message.channel.id == MOD_PANEL_CHANNEL_ID and self._is_mod_panel_message(reaction.message):
                await self._handle_list_violations(reaction, user, guild)

    def _is_spam_log(self, message: discord.Message) -> bool:
        """スパム検出 Embed か判定"""
        if not message.embeds:
            return False
        embed = message.embeds[0]
        return embed.title == "🚨 スパム検出"

    def _is_mod_panel_message(self, message: discord.Message) -> bool:
        """モデレーションパネルメッセージか判定"""
        # 例：特定の絵文字やテキストを含むメッセージ
        return "🔄 リセット" in message.content or "mod_panel" in message.content

    async def _handle_reset(self, reaction: discord.Reaction, user: discord.User, guild: discord.Guild):
        """違反をリセット"""
        target_user_id = None

        # スパムログ Embed の場合
        if reaction.message.embeds:
            embed = reaction.message.embeds[0]
            if embed.title == "🚨 スパム検出":
                # Embed フィールドから "ユーザー情報" を取得
                for field in embed.fields:
                    if field.name == "ユーザー情報":
                        # "ID: 123456789" という形式から抽出
                        lines = field.value.split("\n")
                        for line in lines:
                            if line.startswith("ID:"):
                                try:
                                    target_user_id = int(line.split(":")[-1].strip())
                                    break
                                except ValueError:
                                    pass
                        break

        # mod_panel メッセージの場合
        if not target_user_id:
            content = reaction.message.content
            lines = content.split("\n")
            for line in lines:
                if "ユーザーID:" in line:
                    try:
                        target_user_id = int(line.split(":")[-1].strip())
                        break
                    except ValueError:
                        pass

        if not target_user_id:
            await reaction.message.channel.send(
                f"{user.mention} ユーザーID が見つかりません。",
                delete_after=5
            )
            return

        # 違反をリセット
        spam_guard: SpamGuard = self.bot.spam_guard  # type: ignore[attr-defined]
        spam_guard.reset_violation(target_user_id, guild.id)

        await reaction.message.channel.send(
            f"✅ ユーザーID `{target_user_id}` の違反をリセットしました。",
            delete_after=10
        )

    async def _handle_list_violations(self, reaction: discord.Reaction, user: discord.User, guild: discord.Guild):
        """違反ユーザー一覧を表示"""
        spam_guard: SpamGuard = self.bot.spam_guard  # type: ignore[attr-defined]
        violations = spam_guard.get_all_violations()

        # ギルド内の違反のみフィルタ
        guild_violations = {
            (uid, gid): v for (uid, gid), v in violations.items() if gid == guild.id
        }

        if not guild_violations:
            await reaction.message.channel.send(
                "📋 違反ユーザーはいません。",
                delete_after=10
            )
            return

        lines = ["📋 **違反ユーザー一覧**"]
        for (uid, gid), violation in guild_violations.items():
            level = violation.get_level()
            count = violation.violation_count
            lines.append(f"`{uid}`: レベル **{level}** (違反 {count} 回)")

        embed = discord.Embed(
            title="スパム違反管理",
            description="\n".join(lines),
            color=discord.Color.orange()
        )

        await reaction.message.channel.send(embed=embed, delete_after=30)

    @commands.command(name="modpanel")
    @commands.has_permissions(administrator=True)
    async def create_mod_panel(self, ctx: commands.Context):
        """モデレーションパネルを作成（管理者のみ）"""
        channel = self.bot.get_channel(MOD_PANEL_CHANNEL_ID)
        if not channel:
            await ctx.send("❌ モデレーションパネルチャンネルが見つかりません。")
            return

        embed = discord.Embed(
            title="🛡️ スパム管理パネル",
            description=(
                "このパネルを使用してスパムユーザーを管理できます。\n\n"
                "**使い方：**\n"
                "1. ユーザーIDを記載したメッセージにリアクションを追加\n"
                "2. 🔄 を押すとリセット\n"
                "3. 📋 を押すと違反一覧表示\n\n"
                "**例:**\n"
                "`ユーザーID: 123456789`\n"
                "`レベル: mute`\n"
                "`違反回数: 3`"
            ),
            color=discord.Color.red()
        )
        embed.set_footer(text="mod_panel")

        msg = await channel.send(embed=embed)
        await msg.add_reaction("🔄")
        await msg.add_reaction("📋")

        await ctx.send(f"✅ モデレーションパネルを作成しました。")


async def setup(bot: commands.Bot):
    await bot.add_cog(ModPanel(bot))
