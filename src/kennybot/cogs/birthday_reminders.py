from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, time

import discord
from discord import app_commands
from discord.ext import commands

from src.kennybot.features.birthday import BirthdayReminderRecord, BirthdayReminderStore
from src.kennybot.utils.command_catalog import get_slash_command_meta
from src.kennybot.utils.text import looks_like_web_search_artifact
from src.kennybot.utils.time import now_jst


logger = logging.getLogger(__name__)
URL_RE = re.compile(r"https?://[^\s)>\"]+")

BIRTHDAY_META = get_slash_command_meta("birthday")

ReadableChannel = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.Thread


def _parse_birthday(value: str) -> date:
    text = (value or "").strip()
    if not text:
        raise ValueError("birthday is required")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    if re.fullmatch(r"\d{2}-\d{2}", text):
        return date.fromisoformat(f"2000-{text}")
    raise ValueError("birthday must be YYYY-MM-DD or MM-DD")


def _parse_notify_time(value: str | None) -> time:
    text = (value or "").strip()
    if not text:
        return time(12, 0)
    if not re.fullmatch(r"\d{2}:\d{2}", text):
        raise ValueError("notify_time must be HH:MM")
    parsed = datetime.strptime(text, "%H:%M").time()
    return parsed.replace(second=0, microsecond=0)


def _should_process_now(hour: int) -> bool:
    """Compatibility helper for the legacy default noon processing window."""
    return 12 <= int(hour) <= 13


def _normalize_quote_text(content: str, *, max_chars: int = 80) -> str:
    text = " ".join((content or "").split())
    if not text:
        return ""
    text = text.replace("@", "@\u200b")
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text


def _is_quote_candidate(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return False
    if looks_like_web_search_artifact(text):
        return False
    if URL_RE.fullmatch(text):
        return False
    stripped_urls = URL_RE.sub("", text).strip()
    if not stripped_urls:
        return False
    if len(stripped_urls) < 4 and URL_RE.search(text):
        return False
    return True


def _is_public_talk_channel(channel: discord.abc.GuildChannel | discord.Thread, guild: discord.Guild) -> bool:
    if isinstance(channel, discord.Thread):
        if channel.is_private():
            return False
        if channel.locked:
            return False
        parent = channel.parent
        if parent is None:
            return True
        return parent.permissions_for(guild.default_role).view_channel
    if isinstance(channel, discord.TextChannel):
        perms = channel.permissions_for(guild.default_role)
        return bool(perms.view_channel and perms.read_message_history)
    return False


class BirthdayReminders(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._store = BirthdayReminderStore()
        self._task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self._task = asyncio.create_task(self._run_loop())

    def cog_unload(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _run_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._process_due_birthdays()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Birthday reminder loop failed")
            await asyncio.sleep(60)

    async def _resolve_channel(self, guild: discord.Guild, channel_id: int) -> ReadableChannel | None:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                return None
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread)):
            return channel
        return None

    async def _resolve_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            fetched = await guild.fetch_member(user_id)
        except Exception:
            return None
        return fetched if isinstance(fetched, discord.Member) else None

    async def _build_latest_public_quote(self, guild: discord.Guild) -> str:
        bot_user_id = self.bot.user.id if self.bot.user is not None else None
        best_quote = ""
        best_created_at = None

        channels: list[discord.TextChannel | discord.Thread] = []
        channels.extend(guild.text_channels)
        channels.extend(guild.threads)

        for channel in channels:
            if not _is_public_talk_channel(channel, guild):
                continue
            try:
                async for message in channel.history(limit=20, oldest_first=False):
                    if not _is_quote_candidate(message.content):
                        continue
                    author = getattr(message, "author", None)
                    if author is None:
                        continue
                    author_id = int(getattr(author, "id", 0) or 0)
                    if bot_user_id is not None and author_id == bot_user_id:
                        continue
                    if getattr(author, "bot", False):
                        continue
                    quote = _normalize_quote_text(message.content)
                    if not quote:
                        continue
                    created_at = getattr(message, "created_at", None)
                    if created_at is None:
                        continue
                    if best_created_at is None or created_at > best_created_at:
                        best_created_at = created_at
                        best_quote = quote
                    break
            except Exception:
                logger.debug("Failed to inspect channel history for birthday quote", exc_info=True)

        return best_quote

    async def _process_due_birthdays(self) -> None:
        current = now_jst()
        due = self._store.list_due_for_now(current)
        if not due:
            return

        for record in due:
            guild = self.bot.get_guild(record.guild_id)
            if guild is None:
                continue
            channel = await self._resolve_channel(guild, record.channel_id)
            if channel is None:
                continue

            member = await self._resolve_member(guild, record.user_id) if record.user_id is not None else None
            mention = f"{member.mention} " if member is not None else ""
            name = member.display_name if member is not None else record.display_name
            quote = await self._build_latest_public_quote(guild)
            quote_text = f"\n最後に発言した言葉は「{quote}」でした。" if quote else ""
            text = f"🎉 {mention}{name}さんの誕生日です！{quote_text}"
            try:
                await channel.send(text, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
                self._store.mark_notified(reminder_id=record.id, year=current.year)
            except Exception:
                logger.exception("Failed to send birthday reminder record=%s", record.id)

    def _format_record_line(self, record: BirthdayReminderRecord, guild: discord.Guild) -> str:
        channel = guild.get_channel(record.channel_id)
        channel_text = channel.mention if channel is not None and hasattr(channel, "mention") else f"`{record.channel_id}`"
        member_text = f"<@{record.user_id}>" if record.user_id is not None else record.display_name
        notified = str(record.last_notified_year) if record.last_notified_year is not None else "-"
        return (
            f"#{record.id}: {member_text} / {record.birthday_date.isoformat()} {record.notify_time} / "
            f"{channel_text} / 最終通知年: {notified}"
        )

    @app_commands.command(name=BIRTHDAY_META.name, description=BIRTHDAY_META.description)
    @app_commands.describe(
        action="操作",
        name="通知に使う名前",
        birthday="誕生日を YYYY-MM-DD または MM-DD で指定",
        notify_time="通知時刻を HH:MM で指定（未指定なら 12:00）",
        member="メンションしたい Discord ユーザー",
        channel="通知先チャンネル。未指定ならこのチャンネル",
        record_id="一覧で表示されたID",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="追加", value="add"),
            app_commands.Choice(name="一覧", value="list"),
            app_commands.Choice(name="削除", value="remove"),
        ]
    )
    async def birthday(
        self,
        interaction: discord.Interaction,
        action: str,
        name: str | None = None,
        birthday: str | None = None,
        notify_time: str | None = None,
        member: discord.Member | None = None,
        channel: ReadableChannel | None = None,
        record_id: int | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("このコマンドはサーバー内で使ってください。", ephemeral=True)
            return

        normalized_action = (action or "").strip().lower()
        if normalized_action == "add":
            if not name or not birthday:
                await interaction.response.send_message("追加には `name` と `birthday` が必要です。", ephemeral=True)
                return
            try:
                birthday_date = _parse_birthday(birthday)
            except Exception:
                await interaction.response.send_message("誕生日は `MM-DD` または `YYYY-MM-DD` 形式で入力してください。", ephemeral=True)
                return
            try:
                parsed_notify_time = _parse_notify_time(notify_time)
            except Exception:
                await interaction.response.send_message("通知時刻は `HH:MM` 形式で入力してください。", ephemeral=True)
                return

            target_channel = channel or interaction.channel
            if not isinstance(target_channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread)):
                await interaction.response.send_message("通知先チャンネルを解決できませんでした。", ephemeral=True)
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            record = self._store.upsert_reminder(
                guild_id=interaction.guild.id,
                channel_id=target_channel.id,
                display_name=name,
                birthday_date=birthday_date,
                notify_time=parsed_notify_time.strftime("%H:%M"),
                created_by_id=interaction.user.id,
                user_id=member.id if member is not None else None,
                active=True,
            )
            mention_text = member.mention if member is not None else "なし"
            await interaction.followup.send(
                f"登録しました。ID: `{record.id}` / 名前: `{record.display_name}` / 誕生日: `{record.birthday_date.isoformat()}` / 通知時刻: `{record.notify_time}` / メンション: {mention_text} / 通知先: {target_channel.mention}",
                ephemeral=True,
            )
            return

        if normalized_action == "list":
            records = self._store.list_for_guild(interaction.guild.id)
            if not records:
                await interaction.response.send_message("登録された誕生日はありません。", ephemeral=True)
                return

            lines = [self._format_record_line(record, interaction.guild) for record in records]
            embed = discord.Embed(title="誕生日登録一覧", description="\n".join(lines[:25]), color=discord.Color.blurple())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if normalized_action == "remove":
            if record_id is None:
                await interaction.response.send_message("削除には `record_id` が必要です。", ephemeral=True)
                return
            removed = self._store.remove(guild_id=interaction.guild.id, reminder_id=record_id)
            if not removed:
                await interaction.response.send_message("指定IDの登録が見つかりません。", ephemeral=True)
                return
            await interaction.response.send_message(f"ID `{record_id}` を削除しました。", ephemeral=True)
            return

        await interaction.response.send_message("action は `add` / `list` / `remove` から選んでください。", ephemeral=True)
