# cogs/message_logger.py
# 会話 + リアクション

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord
from discord.ext import commands

from utils.config import (
    OLLAMA_MODEL_DEFAULT,
    CHAT_HISTORY_LINES,
    KEYWORD_REACTIONS,
    PROMPT_TEMPLATE,
    HISTORY_CONTEXT_TEMPLATE,
    USER_NICKNAMES,
    MAX_RESPONSE_LENGTH,
    MAX_RESPONSE_LENGTH_PROMPT,
)
from utils.message_store import MessageStore
from cogs.base import BaseCog
from utils.channel import resolve_log_channel
from utils.text import (
    normalize_user_text,
    is_search_intent,
    strip_ansi_and_ctrl,
)
from guards.spam_guard import SpamGuard
from guards.mod_actions import ModActions


logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))

import random


def get_user_display_name(user_id: int, user_name: str) -> tuple[str, bool]:
    """
    ユーザーの表示名を取得（あだながあれば時々使う）

    Returns:
        (display_name, use_nickname) タプル
        - display_name: 使用する表示名
        - use_nickname: あだなを使用したかどうか
    """
    if user_id in USER_NICKNAMES:
        # 30% の確率であだなを使用
        if random.random() < 0.3:
            return USER_NICKNAMES[user_id], True
    return user_name, False


class MessageLogger(BaseCog):
    """
    メッセージログ＆会話処理

    機能:
    - 通常メッセージへのリアクション（キーワード検索）
    - メンション / リプライへの AI 応答（名前呼び対応）
    """

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        """メッセージイベント（リアクション＆会話）"""
        # Bot自身のメッセージは無視
        if self.bot.user and msg.author.id == self.bot.user.id:
            return

        # DM は対象外
        if msg.guild is None:
            return

        content = msg.content or ""

        # Bot は対象外（ウェブフック含む）
        is_webhook = msg.webhook_id is not None
        is_bot_account = msg.author.bot and not is_webhook
        if is_bot_account or is_webhook:
            return

        # =========================
        # メンション / リプライ判定
        # =========================
        mentioned_bot = self.bot.user in msg.mentions if self.bot.user else False
        is_reply_to_bot = (
            msg.reference
            and msg.reference.resolved
            and isinstance(msg.reference.resolved, discord.Message)
            and self.bot.user
            and msg.reference.resolved.author.id == self.bot.user.id
        )

        # メンション / リプライがない場合はリアクションのみ
        if not mentioned_bot and not is_reply_to_bot:
            # メッセージを履歴に記録
            user_name = msg.author.display_name or msg.author.name or str(msg.author.id)
            store = MessageStore(msg.guild.id, msg.channel.id)
            store.add_message(user_name, content, msg.id, author_id=msg.author.id)

            # =========================
            # スパム検出と処罰
            # =========================
            guard: SpamGuard = self.bot.spam_guard  # type: ignore[attr-defined]

            # 通常メッセージのレート制限チェック
            if not guard.allow_message(msg.author.id, content):
                violation = guard.add_violation(msg.author.id, msg.guild.id)
                level = violation.current_level

                # メッセージを削除
                await ModActions.delete_message(msg, f"スパム（レベル: {level}）")

                # ユーザーがメンバーなら処罰実行
                member = msg.author if isinstance(msg.author, discord.Member) else await msg.guild.fetch_member(msg.author.id)
                punishment_result = ""
                if member and level != "warning":
                    success = await ModActions.execute_level(
                        self.bot,
                        msg.guild,
                        member,
                        level
                    )
                    if success:
                        punishment_result = f"✅ 処罰実行: {level}"
                    else:
                        punishment_result = f"❌ 処罰実行失敗: {level}"

                # =========================
                # スパム詳細情報を Embed で投稿
                # =========================
                embed = discord.Embed(
                    title="🚨 スパム検出",
                    description=f"ユーザー {msg.author.mention} のスパムを検出しました。",
                    color=discord.Color.red(),
                    timestamp=datetime.now(JST)
                )
                embed.add_field(
                    name="ユーザー情報",
                    value=f"名前: {msg.author.display_name or msg.author.name}\nID: {msg.author.id}",
                    inline=False
                )
                embed.add_field(
                    name="削除内容",
                    value=f"```{content[:200]}{'...' if len(content) > 200 else ''}```",
                    inline=False
                )
                embed.add_field(
                    name="違反情報",
                    value=f"レベル: **{level}**\n違反回数: {violation.violation_count}",
                    inline=True
                )
                embed.add_field(
                    name="処罰",
                    value=punishment_result if punishment_result else "警告のみ",
                    inline=True
                )
                embed.set_footer(text=f"チャンネル: {msg.channel.name}")

                # Embed をポスト
                spam_log_msg = await msg.channel.send(embed=embed)

                # 🔄 をリアクションで追加（リセット用）
                await spam_log_msg.add_reaction("🔄")

                # 警告メッセージ送出（cooldown付き）
                if guard.should_warn(msg.author.id):
                    warn_msg = (
                        f"⚠️ {msg.author.mention}\n"
                        f"スパムが検出されました。\n"
                        f"現在のレベル: **{level}** (違反 {violation.violation_count} 回)\n"
                        f"⚠️ 継続するとキックやバンの対象になります。"
                    )
                    await msg.channel.send(warn_msg, delete_after=15)

                await self.bot.process_commands(msg)
                return

            # キーワード -> 絵文字 の対応（config から取得）
            for keyword, emoji in KEYWORD_REACTIONS.items():
                if keyword.lower() in content.lower():
                    try:
                        await msg.add_reaction(emoji)
                    except Exception as e:
                        logger.debug(f"Reaction failed: {e}")

            await self.bot.process_commands(msg)
            return

        # =========================
        # ここから AI 応答処理（メンション or リプライの場合）
        # =========================
        text = normalize_user_text(content)
        if not text:
            await self.bot.process_commands(msg)
            return

        # ユーザー名を取得
        user = msg.author
        user_name = user.display_name or user.name or str(user.id)
        user_display, used_nickname = get_user_display_name(user.id, user_name)

        # スパム対策（AI 呼び出しレート制限）
        guard: SpamGuard = self.bot.spam_guard  # type: ignore[attr-defined]
        if not guard.allow_ai(msg.author.id):
            if guard.should_warn(msg.author.id):
                await msg.channel.send(
                    f"{msg.author.mention}\n短時間に AI 呼び出しが多いので少し待ってください。"
                )
            await self.bot.process_commands(msg)
            return

        # =========================
        # メッセージ履歴を保存・取得
        # =========================
        store = MessageStore(msg.guild.id, msg.channel.id)
        store.add_message(user_name, text, msg.id, author_id=msg.author.id)

        # 前の会話を取得して文脈を作成
        history_text = store.get_recent_context(lines=CHAT_HISTORY_LINES)
        if history_text:
            history_context = HISTORY_CONTEXT_TEMPLATE.format(history=history_text)
        else:
            history_context = ""

        # =========================
        # プロンプトを生成（履歴と表示名を含める）
        # =========================
        prompt = PROMPT_TEMPLATE.format(
            user_display=user_display,
            history_context=history_context,
            user_message=text,
            max_response_length_prompt=MAX_RESPONSE_LENGTH_PROMPT,
        )

        try:
            async with msg.channel.typing():
                # Ollama クライアントで応答生成
                answer = self.bot.ollama_client.chat_simple(
                    model=OLLAMA_MODEL_DEFAULT,
                    prompt=prompt,
                    stream=False,
                )

            answer = (answer or "").strip()
            answer = strip_ansi_and_ctrl(answer)

            if not answer:
                answer = "(応答が空でした)"

            # 応答文字数制限（メンション部分を考慮：メンション約25文字 + 改行）
            if len(answer) > MAX_RESPONSE_LENGTH:
                answer = answer[:MAX_RESPONSE_LENGTH] + "\n...(省略)..."

            # Bot の応答も履歴に保存
            bot_name = self.bot.user.name if self.bot.user else "Bot"
            bot_id = self.bot.user.id if self.bot.user else 0
            store.add_message(bot_name, answer, msg.id, author_id=bot_id)

            # メッセージ送信（メンションのみ）
            final_message = f"{msg.author.mention}\n{answer}"

            # 最終的なメッセージサイズが 2000 を超える場合は切り詰める
            if len(final_message) > 2000:
                # メンション+改行の長さを計算
                mention_overhead = len(f"{msg.author.mention}\n")
                max_answer_len = 2000 - mention_overhead - len("\n...(省略)...")
                answer = answer[:max_answer_len] + "\n...(省略)..."
                final_message = f"{msg.author.mention}\n{answer}"

            await msg.channel.send(final_message)

        except Exception as e:
            logger.exception("AI response failed")
            error_msg = str(e)

            # エラーメッセージを詳しく表示
            if "401" in error_msg or "unauthorized" in error_msg.lower():
                detail = "Ollama 認証エラー: API キー設定を確認してください。"
            elif "prompt:latest" in str(e):
                detail = "モデル「prompt:latest」が見つかりません。ollama list で確認してください。"
            else:
                detail = f"詳細: {error_msg[:100]}"

            await msg.channel.send(
                f"{msg.author.mention}\n内部エラーが発生しました。\n```\n{detail}\n```"
            )

        # コマンド処理へ
        await self.bot.process_commands(msg)
