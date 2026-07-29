import sys
from pathlib import Path
import unittest
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import contextlib


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "discord" not in sys.modules:
    class _DiscordModule(types.ModuleType):
        def __getattr__(self, name):
            placeholder = type(name, (), {})
            setattr(self, name, placeholder)
            return placeholder

    class _DiscordSubmodule(types.ModuleType):
        def __getattr__(self, name):
            placeholder = type(name, (), {})
            setattr(self, name, placeholder)
            return placeholder

    discord = _DiscordModule("discord")
    discord.__path__ = []
    discord.Message = object

    class _AllowedMentions:
        @staticmethod
        def none():
            return None

    class _File:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    discord.AllowedMentions = _AllowedMentions
    discord.File = _File
    discord.abc = _DiscordSubmodule("discord.abc")
    discord.abc.__path__ = []
    discord.abc.Messageable = object
    ext = types.ModuleType("discord.ext")
    ext.__path__ = []
    commands = types.ModuleType("discord.ext.commands")

    class _Cog:
        @classmethod
        def listener(cls, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    class _Bot:
        pass

    commands.Cog = _Cog
    commands.Bot = _Bot
    ext.commands = commands
    discord.ext = ext
    utils = _DiscordSubmodule("discord.utils")
    utils.get = lambda *args, **kwargs: None
    discord.utils = utils
    sys.modules["discord"] = discord
    sys.modules["discord.abc"] = discord.abc
    sys.modules["discord.ext"] = ext
    sys.modules["discord.ext.commands"] = commands
    sys.modules["discord.utils"] = utils

from src.kennybot.cogs.message_logger import MessageLogger
from src.kennybot.guards.mod_actions import ModActions
import discord


class MessageLoggerSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = MessageLogger.__new__(MessageLogger)

    def _setup_normal_chat_logger(
        self,
        *,
        history_context: str = "",
        references: list[str] | None = None,
        web_queries: list[str] | None = None,
        direct_web_answer: str = "",
        recent_mention_window: bool = False,
    ) -> dict[str, list[object]]:
        calls: dict[str, list[object]] = {"armed": []}
        ai_progress_tracker = SimpleNamespace(
            create_ticket=AsyncMock(return_value="ticket"),
            acquire=AsyncMock(),
            release=AsyncMock(),
            render=lambda *_args, **_kwargs: "progress",
        )
        self.logger.bot = SimpleNamespace(
            user=SimpleNamespace(id=999, name="Kennybot"),
            spam_guard=SimpleNamespace(
                allow_message=lambda *_args, **_kwargs: True,
                allow_ai=lambda *_args, **_kwargs: True,
                ai_retry_after=lambda *_args, **_kwargs: 0,
                should_warn=lambda *_args, **_kwargs: False,
                record_everyone_mention=lambda **_kwargs: None,
            ),
            ai_progress_tracker=ai_progress_tracker,
            process_commands=AsyncMock(),
        )
        self.logger._spam_guard_disabled_guilds = set()
        self.logger._claim_message_once = lambda _message_id: True
        self.logger._cfg_int = lambda _key, default=0: default
        self.logger._cfg_nicknames = lambda: {}
        self.logger._is_everyone_mention = lambda *_args, **_kwargs: False
        self.logger._is_kenny_chat = lambda _msg: False
        self.logger._has_recent_mention_window = lambda _msg: recent_mention_window
        self.logger._arm_recent_mention_window = lambda msg: calls["armed"].append(msg)
        self.logger._is_fix_request_report = lambda _text: False
        self.logger._is_runtime_model_query = lambda _text: False
        self.logger._is_capability_query = lambda _text: False
        self.logger._is_server_owner_query = lambda _text: False
        self.logger._is_member_count_query = lambda _text: False
        self.logger._is_top_talker_query = lambda _text: False
        self.logger._is_channel_profile_query = lambda _text: False
        self.logger._is_ai_channel_rate_limited = lambda _channel_id: False
        self.logger._schedule_message_index = lambda *args, **kwargs: None
        self.logger._image_attachments = lambda _msg: []
        self.logger._should_use_recent_image_context = lambda _text: False
        self.logger._sanitize_for_prompt = lambda text, _limit: text.replace("<@999>", "").strip()
        self.logger._resolve_chat_context = AsyncMock(
            return_value=(
                history_context,
                references or [],
                web_queries or [],
                [],
                direct_web_answer,
            )
        )
        self.logger._current_chat_model_name = lambda: "gemini-2.5-flash"
        self.logger._ai_progress_countdowns = SimpleNamespace(
            start_countup=AsyncMock(),
            stop=AsyncMock(),
            get_message=lambda _key: None,
        )
        self.logger._promote_ai_progress_message = AsyncMock()
        self.logger._run_ollama_chat_with_tools = AsyncMock(
            return_value=("自然な返答です。", [], [], [])
        )
        self.logger._sanitize_user_visible_answer = lambda text: text
        self.logger._should_send_letter_file = lambda _text: False
        self.logger._handle_current_info_search_failure = AsyncMock()
        self.logger._send_ai_text_response = AsyncMock(return_value=[])
        self.logger._log_bot_activity_event = AsyncMock()
        return calls

    def _normal_chat_message(
        self,
        content: str,
        *,
        mentions: list[object] | None = None,
    ) -> SimpleNamespace:
        bot_member = SimpleNamespace(
            id=999,
            guild_permissions=SimpleNamespace(kick_members=True, ban_members=True),
        )
        guild = SimpleNamespace(id=10, name="guild", me=bot_member)
        author = SimpleNamespace(
            id=1,
            display_name="author",
            name="author",
            bot=False,
            mention="<@1>",
        )
        channel = SimpleNamespace(id=20, name="channel", guild=guild, send=AsyncMock())
        return SimpleNamespace(
            id=30,
            author=author,
            guild=guild,
            channel=channel,
            content=content,
            mentions=mentions if mentions is not None else [SimpleNamespace(id=999)],
            role_mentions=[],
            webhook_id=None,
            reference=None,
            attachments=[],
        )

    def test_on_message_normal_chat_uses_gemini_and_sends_final_answer(self) -> None:
        import asyncio

        self._setup_normal_chat_logger(history_context="[直近]\nauthor: こんにちは")
        msg = self._normal_chat_message("<@999> こんにちは、雑談しよ")

        with patch("src.kennybot.cogs.message_logger.log_ai_output"):
            asyncio.run(self.logger.on_message(msg))

        self.logger._resolve_chat_context.assert_awaited_once()
        self.assertEqual(self.logger._resolve_chat_context.await_args.kwargs["text"], "こんにちは、雑談しよ")
        self.logger._run_ollama_chat_with_tools.assert_awaited_once()
        run_kwargs = self.logger._run_ollama_chat_with_tools.await_args.kwargs
        self.assertEqual(run_kwargs["model"], "gemini-2.5-flash")
        user_prompt = run_kwargs["messages"][1]["content"]
        self.assertIn("ユーザーに見せる最終回答だけ", user_prompt)
        self.assertIn("1〜3文で簡潔", user_prompt)
        self.assertIn("[直近]\nauthor: こんにちは", user_prompt)
        self.assertIn("最新メッセージ:\nこんにちは、雑談しよ", user_prompt)
        self.logger._send_ai_text_response.assert_awaited_once()
        send_args = self.logger._send_ai_text_response.await_args
        self.assertEqual(send_args.args[1], "自然な返答です。")
        self.assertEqual(send_args.kwargs["prefix"], "<@1>\n")
        self.assertEqual(send_args.kwargs["model_name"], "gemini-2.5-flash")
        self.logger.bot.process_commands.assert_awaited_once_with(msg)

    def test_on_message_url_question_keeps_url_in_prompt_without_forcing_search(self) -> None:
        import asyncio

        url = "https://example.com/docs?id=123"
        self._setup_normal_chat_logger(
            history_context=f"[URL]\nユーザーが共有したURL: {url}",
            references=[url, "source:recent_turns"],
        )
        msg = self._normal_chat_message(f"<@999> このURL見て要点教えて {url}")

        with patch("src.kennybot.cogs.message_logger.log_ai_output"):
            asyncio.run(self.logger.on_message(msg))

        self.logger._handle_current_info_search_failure.assert_not_awaited()
        self.logger._run_ollama_chat_with_tools.assert_awaited_once()
        user_prompt = self.logger._run_ollama_chat_with_tools.await_args.kwargs["messages"][1]["content"]
        self.assertIn("URL先を読んだとは断定せず", user_prompt)
        self.assertIn("ユーザー文面と会話文脈から分かる範囲だけ", user_prompt)
        self.assertIn(url, user_prompt)
        self.assertIn("このURL見て要点教えて", user_prompt)
        send_kwargs = self.logger._send_ai_text_response.await_args.kwargs
        self.assertIn(url, send_kwargs["references"])
        self.assertEqual(send_kwargs["web_queries"], [])

    def test_on_message_mentioned_person_prompt_includes_person_context(self) -> None:
        import asyncio

        target = SimpleNamespace(id=387651883847909376, bot=False, display_name="Kenny", name="kenny")
        history_context = "\n".join(
            [
                "[この会話で明示された人物候補]",
                "- mentioned_1: Kenny (387651883847909376)",
                "この質問に人物が関わるなら、上の mention 候補を author より優先して解釈すること。",
                "",
                "[Kenny のプロフィール]",
                "[メンバープロフィール]",
                "対象: Kenny (387651883847909376)",
            ]
        )
        self._setup_normal_chat_logger(
            history_context=history_context,
            references=["source:member_profile", "source:member_history"],
        )
        msg = self._normal_chat_message(
            "<@999> <@387651883847909376> この人の情報を教えて",
            mentions=[SimpleNamespace(id=999, bot=True), target],
        )

        with patch("src.kennybot.cogs.message_logger.log_ai_output"):
            asyncio.run(self.logger.on_message(msg))

        user_prompt = self.logger._run_ollama_chat_with_tools.await_args.kwargs["messages"][1]["content"]
        self.assertIn("[この会話で明示された人物候補]", user_prompt)
        self.assertIn("mentioned_1: Kenny (387651883847909376)", user_prompt)
        self.assertIn("author より優先", user_prompt)
        self.assertIn("[メンバープロフィール]", user_prompt)
        self.assertIn("最新メッセージ:\nこの人の情報を教えて", user_prompt)
        self.assertNotIn("serverinfo_strict", user_prompt)

    def test_on_message_web_search_prompt_includes_evidence_constraints(self) -> None:
        import asyncio

        history_context = "\n".join(
            [
                "[検索結果の要約]",
                "検索結果",
                "注意: 以下のタイトル・日付・URL・抜粋だけを根拠にし、出典にない具体事項は確認できないと扱うこと。",
                "- 2026-07-29: Mock News",
                "  https://example.com/news",
                "  ローカル preview 用の抜粋です。",
            ]
        )
        self._setup_normal_chat_logger(
            history_context=history_context,
            references=["source:web_search", "https://example.com/news"],
            web_queries=["今日のニュース"],
        )
        msg = self._normal_chat_message("<@999> 今日のニュースを教えて")

        with patch("src.kennybot.cogs.message_logger.log_ai_output"):
            asyncio.run(self.logger.on_message(msg))

        user_prompt = self.logger._run_ollama_chat_with_tools.await_args.kwargs["messages"][1]["content"]
        system_prompt = self.logger._run_ollama_chat_with_tools.await_args.kwargs["messages"][0]["content"]
        self.assertIn("外部検索結果がある場合", user_prompt)
        self.assertIn("タイトル・日付・URL・抜粋だけを根拠", user_prompt)
        self.assertIn("検索結果", user_prompt)
        self.assertIn("https://example.com/news", user_prompt)
        self.assertIn("最新メッセージ:\n今日のニュースを教えて", user_prompt)
        self.assertIn("現在日時", system_prompt)
        self.assertIn("確認できた日付なしに断定しない", system_prompt)

    def test_capability_query_prompt_includes_full_feature_catalog(self) -> None:
        import asyncio

        ai_progress_tracker = SimpleNamespace(
            create_ticket=AsyncMock(return_value="ticket"),
            acquire=AsyncMock(),
            release=AsyncMock(),
            render=lambda *_args, **_kwargs: "progress",
        )
        self.logger.bot = SimpleNamespace(ai_progress_tracker=ai_progress_tracker)
        self.logger._is_ai_channel_rate_limited = lambda _channel_id: False
        self.logger._is_runtime_model_query = lambda _text: False
        self.logger._build_channel_profile_block = lambda **_kwargs: "[PROFILE]\nKennybot channel"
        self.logger._build_rag_context = lambda *args, **kwargs: (
            "[RAG]\n追加Q&A: 追加の使い方"
            if kwargs.get("capability_only")
            else ""
        )
        self.logger._format_git_updates = lambda count=4: ""
        self.logger._is_update_query = lambda _text: False
        self.logger._current_chat_model_name = lambda: "gemini-2.5-flash"
        self.logger._ai_progress_countdowns = SimpleNamespace(
            start_countup=AsyncMock(),
            stop=AsyncMock(),
            get_message=lambda _key: None,
        )
        self.logger._run_ollama_text = AsyncMock(return_value="機能説明です。")
        self.logger._send_ai_text_response = AsyncMock(return_value=[])
        self.logger._log_bot_activity_event = AsyncMock()
        channel = SimpleNamespace(id=20, guild=SimpleNamespace(id=10), send=AsyncMock())
        source_msg = SimpleNamespace(
            id=30,
            guild=SimpleNamespace(id=10),
            author=SimpleNamespace(id=1),
        )

        asyncio.run(
            self.logger._answer_capability_query(
                channel,
                "あなたの機能を説明して",
                mention="<@1>",
                source_msg=source_msg,
                channel_id=20,
            )
        )

        prompt = self.logger._run_ollama_text.await_args.kwargs["prompt"]
        self.assertIn("機能説明なら、資料から確認できる範囲をカテゴリごとにできるだけ漏れなく", prompt)
        self.assertIn("確認できたものは省かず", prompt)
        self.assertIn("[HELP / 会話機能]", prompt)
        self.assertIn("[HELP / 案内・検索機能]", prompt)
        self.assertIn("[HELP / 議事録機能]", prompt)
        for command in (
            "/help",
            "/bot_info",
            "/ping",
            "/summarize_recent",
            "/set_recent_window",
            "/config",
            "/model_list",
            "/model_change",
            "/minutes",
            "/reaction_role_set",
            "/reaction_role_remove",
            "/reaction_role_list",
            "/modpanel",
            "/birthday",
            "/tts",
            "/game",
            "/timer",
            "/vc_control",
            "/group_match",
            "/vrchat_world",
            "/vrc_user",
        ):
            self.assertIn(command, prompt)
        self.assertIn("mode=ワードウルフ", prompt)
        self.assertIn("mode=人狼役職配布", prompt)
        self.assertIn("追加Q&A: 追加の使い方", prompt)
        self.logger._send_ai_text_response.assert_awaited_once()

    def test_on_message_followup_uses_recent_mention_window_and_previous_context(self) -> None:
        import asyncio

        calls = self._setup_normal_chat_logger(
            history_context="[前の会話]\nauthor: Pythonの乱数の話\nKennybot: randomを使います",
            recent_mention_window=True,
        )
        msg = self._normal_chat_message("じゃあそれを短くして", mentions=[])

        with patch("src.kennybot.cogs.message_logger.log_ai_output"):
            asyncio.run(self.logger.on_message(msg))

        self.assertEqual(calls["armed"], [msg, msg])
        self.logger._resolve_chat_context.assert_awaited_once()
        self.assertEqual(self.logger._resolve_chat_context.await_args.kwargs["text"], "じゃあそれを短くして")
        user_prompt = self.logger._run_ollama_chat_with_tools.await_args.kwargs["messages"][1]["content"]
        self.assertIn("メッセージが複数あっても最新のメッセージを優先", user_prompt)
        self.assertIn("[前の会話]\nauthor: Pythonの乱数の話", user_prompt)
        self.assertIn("最新メッセージ:\nじゃあそれを短くして", user_prompt)
        self.logger._send_ai_text_response.assert_awaited_once()
        self.logger.bot.process_commands.assert_awaited_once_with(msg)

    def test_kenny_chat_source_delete_does_not_delete_mirrors_by_default(self) -> None:
        deleted: list[int] = []

        class FakeMessage:
            def __init__(self, message_id: int) -> None:
                self.id = message_id

            async def delete(self) -> None:
                deleted.append(self.id)

        class FakeTextChannel:
            def get_partial_message(self, message_id: int) -> FakeMessage:
                return FakeMessage(message_id)

        discord.TextChannel = FakeTextChannel
        self.logger.bot = SimpleNamespace(get_channel=lambda _channel_id: FakeTextChannel())
        self.logger._is_kenny_chat = lambda _msg: True
        self.logger._kenny_chat_delete_mirrors_on_source_delete = lambda: False
        self.logger._kenny_chat_mirrors = {10: [(20, 30)]}
        self.logger._kenny_chat_reverse = {30: 10}
        msg = SimpleNamespace(id=10, author=SimpleNamespace(bot=False))

        import asyncio

        asyncio.run(self.logger.on_message_delete(msg))

        self.assertEqual(deleted, [])
        self.assertEqual(self.logger._kenny_chat_mirrors, {})
        self.assertEqual(self.logger._kenny_chat_reverse, {})

    def test_kenny_chat_source_delete_can_delete_mirrors_when_enabled(self) -> None:
        deleted: list[int] = []

        class FakeMessage:
            def __init__(self, message_id: int) -> None:
                self.id = message_id

            async def delete(self) -> None:
                deleted.append(self.id)

        class FakeTextChannel:
            def get_partial_message(self, message_id: int) -> FakeMessage:
                return FakeMessage(message_id)

        discord.TextChannel = FakeTextChannel
        self.logger.bot = SimpleNamespace(get_channel=lambda _channel_id: FakeTextChannel())
        self.logger._is_kenny_chat = lambda _msg: True
        self.logger._kenny_chat_delete_mirrors_on_source_delete = lambda: True
        self.logger._kenny_chat_mirrors = {10: [(20, 30)]}
        self.logger._kenny_chat_reverse = {30: 10}
        msg = SimpleNamespace(id=10, author=SimpleNamespace(bot=False))

        import asyncio

        asyncio.run(self.logger.on_message_delete(msg))

        self.assertEqual(deleted, [30])
        self.assertEqual(self.logger._kenny_chat_mirrors, {})
        self.assertEqual(self.logger._kenny_chat_reverse, {})

    def test_context_target_candidates_prefers_reply_then_mentions(self) -> None:
        author = SimpleNamespace(
            id=1,
            display_name="author",
            name="author",
            bot=False,
            mention="<@1>",
        )
        replied = SimpleNamespace(id=2, display_name="reply", name="reply", bot=False)
        mention = SimpleNamespace(id=3, display_name="mention", name="mention", bot=False)
        msg = SimpleNamespace(
            author=author,
            reference=SimpleNamespace(resolved=SimpleNamespace(author=replied)),
            mentions=[replied, mention],
        )

        targets = self.logger._context_target_candidates(msg)

        self.assertEqual(targets["author"], (1, "author"))
        self.assertEqual(targets["replied_user"], (2, "reply"))
        self.assertEqual(targets["mentioned_1"], (3, "mention"))

    def test_person_lookup_plan_promotes_mentioned_target(self) -> None:
        plan = [
            {"source": "recent_user_history"},
            {"source": "recent_turns", "limit": 4},
        ]
        targets = {
            "author": (1, "author"),
            "replied_user": (2, "reply"),
            "mentioned_1": (3, "mention"),
        }

        adjusted = self.logger._prioritize_mentioned_person_plan(
            plan=plan,
            text="この人のプロフィールを教えて",
            target_candidates=targets,
            user_lines=12,
        )

        self.assertEqual(
            [item["source"] for item in adjusted[:4]],
            ["member_history", "recent_turns"],
        )
        self.assertEqual(adjusted[0]["target"], "mentioned_1")

    def test_person_lookup_query_detection(self) -> None:
        self.assertTrue(self.logger._is_person_lookup_query("この人のプロフィールを教えて"))
        self.assertTrue(self.logger._is_person_lookup_query("<@123> の情報を教えて"))
        self.assertTrue(self.logger._is_person_lookup_query("最後の投稿ある？"))
        self.assertFalse(self.logger._is_person_lookup_query("このサーバーはなにするところ？"))
        self.assertFalse(self.logger._is_person_lookup_query("今日のニュースを教えて"))
        self.assertFalse(self.logger._is_person_lookup_query("使い方を教えて"))

    def test_mentioned_person_lookup_query_detection(self) -> None:
        self.logger.bot = SimpleNamespace(user=SimpleNamespace(id=999))
        msg = SimpleNamespace(
            mentions=[
                SimpleNamespace(id=999, bot=True),
                SimpleNamespace(id=387651883847909376, bot=False),
            ]
        )

        self.assertTrue(self.logger._is_mentioned_person_lookup_query(msg, "この人の情報を教えて"))
        self.assertFalse(self.logger._is_mentioned_person_lookup_query(msg, "このサーバーの情報を教えて"))

    def test_channel_profile_query_detection(self) -> None:
        self.assertTrue(self.logger._is_channel_profile_query("このサーバーは何のやつ？"))
        self.assertTrue(self.logger._is_channel_profile_query("このサーバーってなにをするところ？"))
        self.assertTrue(self.logger._is_channel_profile_query("このサーバの情報を教えて"))
        self.assertTrue(self.logger._is_channel_profile_query("サーバの説明を教えて"))
        self.assertFalse(self.logger._is_channel_profile_query("このBotの機能を教えて"))

    def test_gas_price_queries_require_web_search(self) -> None:
        self.assertTrue(self.logger._needs_web_search_for_accuracy("柏市のレモンガスとENEOSガスの料金を比較して"))
        self.assertTrue(self.logger._needs_web_search_for_accuracy("LPガスの供給エリアを教えて"))
        self.assertTrue(self.logger._needs_web_search_for_accuracy("都市ガスの契約先はどこ？"))

    def test_direct_web_answer_warns_about_stale_dates(self) -> None:
        answer = self.logger._build_direct_web_search_answer("検索結果\n- 2024年4月: 料金表")
        self.assertIn("古い日付", answer)
        self.assertIn("最新条件は公式情報", answer)

    def test_build_planned_context_channel_profile_is_strict(self) -> None:
        guild = SimpleNamespace(id=10, name="Test Guild")
        channel = SimpleNamespace(
            id=20,
            name="general",
            guild=guild,
            category=SimpleNamespace(name="案内"),
            topic="サーバーの案内です",
        )
        author = SimpleNamespace(
            id=1,
            display_name="author",
            name="author",
            bot=False,
            mention="<@1>",
        )
        msg = SimpleNamespace(
            author=author,
            guild=guild,
            channel=channel,
            content="このサーバーは何するところ？",
        )
        self.logger._build_channel_profile_block = lambda **_kwargs: "【profile】\nこの場所の正式プロフィール"
        self.logger._build_retrieval_plan = AsyncMock(side_effect=AssertionError("planner should not run"))

        import asyncio

        context, refs, web_queries, details, direct_web_answer = asyncio.run(
            self.logger._build_planned_context(
                msg=msg,
                user_display="author",
                text=msg.content,
            )
        )

        self.assertIn("[現在の場所のメタ情報]", context)
        self.assertIn("サーバー名: Test Guild", context)
        self.assertIn("チャンネル名: general", context)
        self.assertIn("この場所の正式プロフィール", context)
        self.assertIn("source:location_meta", refs)
        self.assertIn("source:serverinfo", refs)
        self.assertEqual(web_queries, [])
        self.assertEqual(direct_web_answer, "")
        self.assertIn("planner_response_mode=serverinfo_strict", details)
        self.assertIn("location_meta=on", details)
        self.assertIn("serverinfo=channel_profile", details)
        self.assertNotIn("recent_turns", "\n".join(details))
        self.assertNotIn("local_knowledge", "\n".join(details))

    def test_force_channel_profile_plan_prefers_channel_profile_only(self) -> None:
        plan = [
            {"source": "recent_turns", "limit": 8},
            {"source": "local_knowledge", "query": "this bot"},
            {"source": "channel_profile"},
        ]

        forced = self.logger._force_channel_profile_plan(
            plan=plan,
            text="ここはどんなサーバーですか？",
            channel_profile_available=True,
        )

        self.assertEqual(forced, [{"source": "channel_profile"}])

    def test_spam_guard_disables_without_kick_or_ban_permissions(self) -> None:
        bot_member = SimpleNamespace(
            id=999,
            guild_permissions=SimpleNamespace(kick_members=False, ban_members=False),
        )
        guild = SimpleNamespace(me=bot_member, get_member=lambda _member_id: None)

        self.assertTrue(
            ModActions.should_disable_spam_guard(
                SimpleNamespace(user=SimpleNamespace(id=999)),
                guild,
            )
        )

    def test_spam_guard_stays_enabled_when_kick_or_ban_is_available(self) -> None:
        bot_member = SimpleNamespace(
            id=999,
            guild_permissions=SimpleNamespace(kick_members=True, ban_members=False),
        )
        guild = SimpleNamespace(me=bot_member, get_member=lambda _member_id: None)

        self.assertFalse(
            ModActions.should_disable_spam_guard(
                SimpleNamespace(user=SimpleNamespace(id=999)),
                guild,
            )
        )

    def test_collect_message_ids_deduplicates_and_preserves_order(self) -> None:
        messages = [
            {"id": 101},
            {"id": "102"},
            {"id": 101},
            {"id": 0},
            {"id": None},
            {"id": 103},
        ]

        ids = self.logger._collect_message_ids(messages)

        self.assertEqual(ids, ["101", "102", "103"])

    def test_sanitize_user_visible_answer_rewrites_reference_detail_label(self) -> None:
        text = "参照概要と参照詳細を確認してください。"

        sanitized = self.logger._sanitize_user_visible_answer(text)

        self.assertIn("参照元の概要", sanitized)
        self.assertIn("参照元の詳細", sanitized)

    def test_sanitize_user_visible_answer_keeps_confirmed_commands_only(self) -> None:
        text = "関連コマンド: /help /totally_fake_command"

        sanitized = self.logger._sanitize_user_visible_answer(text)

        self.assertIn("/help", sanitized)
        self.assertIn("totally_fake_command", sanitized)
        self.assertNotIn("/totally_fake_command", sanitized)

    def test_send_ai_text_response_adds_review_reaction_and_context(self) -> None:
        import asyncio

        reactions: list[str] = []

        class FakeMessage:
            def __init__(self, message_id: int, channel: object) -> None:
                self.id = message_id
                self.channel = channel
                self.guild = SimpleNamespace(id=10)

            async def add_reaction(self, emoji: str) -> None:
                reactions.append(emoji)

        class FakeChannel:
            id = 20
            guild = SimpleNamespace(id=10)

            async def send(self, content: str, **_kwargs: object) -> FakeMessage:
                self.content = content
                return FakeMessage(30, self)

        channel = FakeChannel()
        source_msg = SimpleNamespace(
            id=40,
            author=SimpleNamespace(id=50),
        )
        self.logger._ai_answer_reviews = {}

        sent = asyncio.run(
            self.logger._send_ai_text_response(
                channel,
                "回答です",
                prefix="<@50>\n",
                source_msg=source_msg,
                question_text="質問です",
                model_name="model-a",
                references=["source:test"],
            )
        )

        self.assertEqual(len(sent), 1)
        self.assertEqual(reactions, ["🤔"])
        context = self.logger._ai_answer_reviews[30]
        self.assertEqual(context.question_message_id, 40)
        self.assertEqual(context.question_author_id, 50)
        self.assertEqual(context.question_text, "質問です")
        self.assertEqual(context.answer_text, "回答です")
        self.assertEqual(context.references, ("source:test",))

    def test_thinking_reaction_routes_saved_ai_answer_to_review(self) -> None:
        import asyncio

        calls: list[tuple[int, str]] = []
        self.logger.bot = SimpleNamespace(user=SimpleNamespace(id=999))
        self.logger._ai_answer_reviews = {
            30: SimpleNamespace(question_text="質問", answer_text="回答")
        }

        async def fake_review(payload: object, context: object) -> None:
            calls.append((payload.message_id, context.question_text))

        self.logger._review_ai_answer_if_needed = fake_review

        asyncio.run(
            self.logger.on_raw_reaction_add(
                SimpleNamespace(user_id=50, message_id=30, emoji="🤔")
            )
        )
        asyncio.run(
            self.logger.on_raw_reaction_add(
                SimpleNamespace(user_id=50, message_id=31, emoji="🤔")
            )
        )
        asyncio.run(
            self.logger.on_raw_reaction_add(
                SimpleNamespace(user_id=50, message_id=30, emoji="✅")
            )
        )

        self.assertEqual(calls, [(30, "質問")])

    def test_web_search_fallback_plan_prefers_web_search_for_latest_queries(self) -> None:
        self.assertTrue(self.logger._needs_web_search_for_accuracy("今日のニュースは？"))
        self.assertTrue(self.logger._needs_web_search_for_accuracy("ダイソーで売ってる？"))

        plan = self.logger._fallback_retrieval_plan(
            text="今日のニュースは？",
            user_lines=12,
            channel_lines=8,
            has_profile=False,
        )

        self.assertGreaterEqual(len(plan), 2)
        self.assertEqual(plan[0]["source"], "web_search")
        self.assertEqual(plan[1]["source"], "recent_turns")

    def test_on_message_routes_channel_profile_directly(self) -> None:
        bot_member = SimpleNamespace(
            id=999,
            guild_permissions=SimpleNamespace(kick_members=True, ban_members=True),
        )
        guild = SimpleNamespace(id=10, name="guild", me=bot_member)
        self.logger.bot = SimpleNamespace(
            user=SimpleNamespace(id=999, name="Kennybot"),
            spam_guard=SimpleNamespace(
                allow_message=lambda *_args, **_kwargs: True,
                allow_ai=lambda *_args, **_kwargs: True,
            ),
            process_commands=AsyncMock(),
        )
        self.logger._is_kenny_chat = lambda _msg: False
        self.logger._has_recent_mention_window = lambda _msg: False
        self.logger._is_runtime_model_query = lambda _text: False
        self.logger._is_capability_query = lambda _text: False
        self.logger._answer_channel_profile_query = AsyncMock()
        self.logger._answer_capability_query = AsyncMock()
        self.logger._send_runtime_model_reply = AsyncMock()
        self.logger._schedule_message_index = lambda *args, **kwargs: None
        self.logger._arm_recent_mention_window = lambda _msg: None

        author = SimpleNamespace(
            id=1,
            display_name="author",
            name="author",
            bot=False,
            mention="<@1>",
        )
        channel = SimpleNamespace(id=20, name="channel", guild=guild, send=AsyncMock())
        msg = SimpleNamespace(
            author=author,
            guild=guild,
            channel=channel,
            content="このチャンネルは何をする場所？",
            mentions=[SimpleNamespace(id=999)],
            webhook_id=None,
            reference=None,
        )

        import asyncio

        asyncio.run(self.logger.on_message(msg))

        self.logger._answer_channel_profile_query.assert_awaited_once()
        self.logger._answer_capability_query.assert_not_awaited()
        self.logger._send_runtime_model_reply.assert_not_awaited()
        self.logger.bot.process_commands.assert_awaited_once_with(msg)

    def test_on_message_does_not_route_mentioned_person_lookup_to_channel_profile(self) -> None:
        bot_member = SimpleNamespace(
            id=999,
            bot=True,
            guild_permissions=SimpleNamespace(kick_members=True, ban_members=True),
        )
        target_member = SimpleNamespace(id=387651883847909376, bot=False, display_name="Kenny", name="kenny")
        guild = SimpleNamespace(id=10, name="guild", me=bot_member)
        ai_progress_tracker = SimpleNamespace(
            create_ticket=AsyncMock(return_value="ticket"),
            acquire=AsyncMock(),
            release=AsyncMock(),
            render=lambda *_args, **_kwargs: "progress",
        )
        self.logger.bot = SimpleNamespace(
            user=SimpleNamespace(id=999, name="Kennybot"),
            spam_guard=SimpleNamespace(
                allow_message=lambda *_args, **_kwargs: True,
                allow_ai=lambda *_args, **_kwargs: True,
                ai_retry_after=lambda *_args, **_kwargs: 0,
                should_warn=lambda *_args, **_kwargs: False,
            ),
            ai_progress_tracker=ai_progress_tracker,
            process_commands=AsyncMock(),
        )
        self.logger._spam_guard_disabled_guilds = set()
        self.logger._claim_message_once = lambda _message_id: True
        self.logger._cfg_int = lambda _key, default=0: default
        self.logger._cfg_nicknames = lambda: {}
        self.logger._is_kenny_chat = lambda _msg: False
        self.logger._has_recent_mention_window = lambda _msg: False
        self.logger._arm_recent_mention_window = lambda _msg: None
        self.logger._is_fix_request_report = lambda _text: False
        self.logger._is_runtime_model_query = lambda _text: False
        self.logger._is_capability_query = lambda _text: False
        self.logger._is_server_owner_query = lambda _text: False
        self.logger._is_member_count_query = lambda _text: False
        self.logger._is_top_talker_query = lambda _text: False
        self.logger._is_ai_channel_rate_limited = lambda _channel_id: False
        self.logger._schedule_message_index = lambda *args, **kwargs: None
        self.logger._image_attachments = lambda _msg: []
        self.logger._sanitize_for_prompt = lambda text, _limit: text.replace("<@999>", "").strip()
        self.logger._answer_channel_profile_query = AsyncMock()
        self.logger._resolve_chat_context = AsyncMock(return_value=("", [], [], [], ""))
        self.logger._current_chat_model_name = lambda: "gemini-2.5-flash"
        self.logger._cfg_ai_timeout = lambda: 1
        self.logger._ai_progress_countdowns = SimpleNamespace(
            start_countup=AsyncMock(),
            stop=AsyncMock(),
            get_message=lambda _key: None,
        )
        self.logger._render_references_footer = lambda *_args, **_kwargs: ""
        self.logger._build_final_answer = AsyncMock(return_value="Kenny さんについて確認します。")
        self.logger._run_ollama_chat_with_tools = AsyncMock(
            return_value=("Kenny さんについて確認します。", [], [], [])
        )
        self.logger._save_ai_answer_for_review = lambda *args, **kwargs: None
        self.logger._add_ai_review_reaction = AsyncMock()
        self.logger._log_bot_activity_event = AsyncMock()
        self.logger._mark_ai_channel_used = lambda _channel_id: None
        self.logger._should_use_recent_image_context = lambda _text: False

        author = SimpleNamespace(
            id=1,
            display_name="author",
            name="author",
            bot=False,
            mention="<@1>",
        )
        channel = SimpleNamespace(id=20, name="channel", guild=guild, send=AsyncMock())
        msg = SimpleNamespace(
            id=123,
            author=author,
            guild=guild,
            channel=channel,
            content="<@999> <@387651883847909376> この人の情報を教えて",
            mentions=[bot_member, target_member],
            role_mentions=[],
            webhook_id=None,
            reference=None,
            attachments=[],
        )

        import asyncio

        asyncio.run(self.logger.on_message(msg))

        self.logger._answer_channel_profile_query.assert_not_awaited()
        self.logger._resolve_chat_context.assert_awaited_once()
        self.logger.bot.process_commands.assert_awaited_once_with(msg)

    def test_on_message_routes_server_stats_directly(self) -> None:
        bot_member = SimpleNamespace(
            id=999,
            guild_permissions=SimpleNamespace(kick_members=True, ban_members=True),
        )
        guild = SimpleNamespace(id=10, name="guild", me=bot_member)
        self.logger.bot = SimpleNamespace(
            user=SimpleNamespace(id=999),
            spam_guard=SimpleNamespace(
                allow_message=lambda *_args, **_kwargs: True,
                allow_ai=lambda *_args, **_kwargs: True,
            ),
            process_commands=AsyncMock(),
        )
        self.logger._is_kenny_chat = lambda _msg: False
        self.logger._has_recent_mention_window = lambda _msg: False
        self.logger._is_runtime_model_query = lambda _text: False
        self.logger._is_capability_query = lambda _text: False
        self.logger._answer_server_stats_query = AsyncMock()
        self.logger._answer_channel_profile_query = AsyncMock()
        self.logger._answer_capability_query = AsyncMock()
        self.logger._send_runtime_model_reply = AsyncMock()
        self.logger._schedule_message_index = lambda *args, **kwargs: None
        self.logger._arm_recent_mention_window = lambda _msg: None

        author = SimpleNamespace(
            id=1,
            display_name="author",
            name="author",
            bot=False,
            mention="<@1>",
        )
        channel = SimpleNamespace(id=20, name="channel", guild=guild, send=AsyncMock())
        msg = SimpleNamespace(
            author=author,
            guild=guild,
            channel=channel,
            content="このサーバーは誰が一番話してる？",
            mentions=[SimpleNamespace(id=999)],
            webhook_id=None,
            reference=None,
        )

        import asyncio

        asyncio.run(self.logger.on_message(msg))

        self.logger._answer_server_stats_query.assert_awaited_once()
        self.logger._answer_channel_profile_query.assert_not_awaited()
        self.logger.bot.process_commands.assert_awaited_once_with(msg)

    def test_on_message_fix_request_marks_followup_activity_as_codex_mode(self) -> None:
        bot_member = SimpleNamespace(
            id=999,
            guild_permissions=SimpleNamespace(kick_members=True, ban_members=True),
        )
        guild = SimpleNamespace(id=10, name="guild", me=bot_member)
        ai_progress_tracker = SimpleNamespace(
            create_ticket=AsyncMock(return_value="ticket"),
            acquire=AsyncMock(),
            release=AsyncMock(),
            render=lambda *_args, **_kwargs: "progress",
        )
        self.logger.bot = SimpleNamespace(
            user=SimpleNamespace(id=999, name="Kennybot"),
            spam_guard=SimpleNamespace(
                allow_message=lambda *_args, **_kwargs: True,
                allow_ai=lambda *_args, **_kwargs: True,
                ai_retry_after=lambda *_args, **_kwargs: 0,
                should_warn=lambda *_args, **_kwargs: False,
            ),
            ai_progress_tracker=ai_progress_tracker,
            process_commands=AsyncMock(),
        )
        self.logger._cfg_int = lambda _key, default=0: default
        self.logger._cfg_nicknames = lambda: {}
        self.logger._is_kenny_chat = lambda _msg: False
        self.logger._has_recent_mention_window = lambda _msg: False
        self.logger._is_fix_request_report = lambda _text: True
        self.logger._log_fix_request = AsyncMock()
        self.logger._is_runtime_model_query = lambda _text: False
        self.logger._is_capability_query = lambda _text: False
        self.logger._is_server_owner_query = lambda _text: False
        self.logger._is_member_count_query = lambda _text: False
        self.logger._is_top_talker_query = lambda _text: False
        self.logger._is_channel_profile_query = lambda _text: False
        self.logger._is_ai_channel_rate_limited = lambda _channel_id: False
        self.logger._schedule_message_index = lambda *args, **kwargs: None
        self.logger._arm_recent_mention_window = lambda _msg: None
        self.logger._resolve_chat_context = AsyncMock(return_value=("context", [], [], [], ""))
        self.logger._current_chat_model_name = lambda: "test-model"
        self.logger._ai_progress_countdowns = SimpleNamespace(
            start_countup=AsyncMock(),
            stop=AsyncMock(),
        )
        self.logger._promote_ai_progress_message = AsyncMock()
        self.logger._run_ollama_chat_with_tools = AsyncMock(
            return_value=("修正後の案内です。", [], [], [])
        )
        self.logger._sanitize_user_visible_answer = lambda text: text
        self.logger._extract_urls = lambda _text: []
        self.logger._should_send_letter_file = lambda _text: False
        self.logger._log_bot_activity_event = AsyncMock()

        author = SimpleNamespace(
            id=1,
            display_name="author",
            name="author",
            bot=False,
            mention="<@1>",
        )
        channel = SimpleNamespace(id=20, name="channel", guild=guild, send=AsyncMock())
        msg = SimpleNamespace(
            id=30,
            author=author,
            guild=guild,
            channel=channel,
            content="<@999> さっきの説明違うから修正して",
            mentions=[SimpleNamespace(id=999)],
            webhook_id=None,
            reference=None,
        )

        import asyncio

        asyncio.run(self.logger.on_message(msg))

        self.logger._log_fix_request.assert_awaited_once_with(msg, "さっきの説明違うから修正して")
        self.assertGreaterEqual(self.logger._log_bot_activity_event.await_count, 1)
        final_log_kwargs = self.logger._log_bot_activity_event.await_args.kwargs
        self.assertTrue(final_log_kwargs["codex_mode"])
        self.assertEqual(final_log_kwargs["processing"], "修正モード応答")

    def test_on_message_logs_authoritative_fix_request_without_mention(self) -> None:
        bot_member = SimpleNamespace(
            id=999,
            guild_permissions=SimpleNamespace(kick_members=True, ban_members=True),
        )
        guild = SimpleNamespace(id=10, name="guild", me=bot_member)
        self.logger.bot = SimpleNamespace(
            user=SimpleNamespace(id=999, name="Kennybot"),
            spam_guard=SimpleNamespace(
                allow_message=lambda *_args, **_kwargs: True,
                allow_ai=lambda *_args, **_kwargs: True,
            ),
            process_commands=AsyncMock(),
        )
        self.logger._cfg_int = lambda _key, default=0: default
        self.logger._cfg_int_list = lambda _key: [387651883847909376]
        self.logger._is_kenny_chat = lambda _msg: False
        self.logger._has_recent_mention_window = lambda _msg: False
        self.logger._is_fix_request_report = lambda _text: True
        self.logger._log_fix_request = AsyncMock()
        self.logger._schedule_message_index = lambda *args, **kwargs: None

        author = SimpleNamespace(
            id=387651883847909376,
            display_name="admin",
            name="admin",
            bot=False,
            mention="<@387651883847909376>",
        )
        channel = SimpleNamespace(id=20, name="channel", guild=guild, send=AsyncMock())
        msg = SimpleNamespace(
            id=30,
            author=author,
            guild=guild,
            channel=channel,
            content="この説明違うから直して",
            mentions=[],
            webhook_id=None,
            reference=None,
        )

        import asyncio

        asyncio.run(self.logger.on_message(msg))

        self.logger._log_fix_request.assert_awaited_once_with(msg, "この説明違うから直して")
        self.logger.bot.process_commands.assert_awaited_once_with(msg)

    def test_channel_profile_query_runs_llm_even_with_fallback_answer(self) -> None:
        self.logger.root = ROOT
        self.logger._is_ai_channel_rate_limited = lambda _channel_id: False
        self.logger._build_location_meta_block = lambda **_kwargs: "[現在の場所のメタ情報]\nサーバー名: guild"
        self.logger._build_channel_profile_block = lambda **_kwargs: "[この場所の正式プロフィール]\n交流用の場所です。"
        self.logger._sanitize_user_visible_answer = lambda text: text
        self.logger._collect_reference_labels = lambda *_args, **_kwargs: ["source:serverinfo"]
        self.logger._send_chunked_text = AsyncMock()
        self.logger._log_bot_activity_event = AsyncMock()
        self.logger._run_ollama_text = AsyncMock(return_value="LLMで整形した説明です。")
        self.logger.bot = SimpleNamespace(
            ai_progress_tracker=SimpleNamespace(
                create_ticket=AsyncMock(return_value="ticket"),
                acquire=AsyncMock(),
                release=AsyncMock(),
                render=lambda *_args, **_kwargs: "progress",
            )
        )
        self.logger._ai_progress_countdowns = SimpleNamespace(
            start_countup=AsyncMock(),
            stop=AsyncMock(),
        )

        guild = SimpleNamespace(id=10, name="guild")

        @contextlib.asynccontextmanager
        async def _typing():
            yield

        channel = SimpleNamespace(
            id=20,
            guild=guild,
            typing=_typing,
        )
        author = SimpleNamespace(mention="<@1>")
        source_msg = SimpleNamespace(guild=guild, channel=channel, author=author)

        import asyncio

        asyncio.run(
            self.logger._answer_channel_profile_query(
                channel,
                "このサーバーは何するところ？",
                mention=author.mention,
                source_msg=source_msg,
                channel_id=20,
            )
        )

        self.logger._run_ollama_text.assert_awaited_once()
        self.logger._send_chunked_text.assert_awaited_once()
        sent_answer = self.logger._send_chunked_text.await_args.kwargs["prefix"]
        self.assertEqual(sent_answer, "<@1>\n")
        output_text = self.logger._send_chunked_text.await_args.args[1]
        self.assertEqual(output_text, "LLMで整形した説明です。")

    def test_log_fix_request_awaits_previous_turn_context(self) -> None:
        self.logger._infer_fix_request_details = lambda _text: ("応答品質", "修正する")
        self.logger._extract_previous_turn_context = AsyncMock(return_value=("前回の質問", "前回の返答"))
        self.logger._decide_fix_mode = AsyncMock(
            return_value={
                "target_area": "応答品質",
                "planned_fix": "修正する",
                "user_reply_hint": "確認します",
                "activate": True,
            }
        )
        self.logger._build_repair_user_reply = AsyncMock(return_value="確認します")
        self.logger._append_fix_request_to_rag = lambda **_kwargs: []
        self.logger._dispatch_codex_repair_logging = AsyncMock()
        self.logger._log_bot_activity_event = AsyncMock()
        self.logger.bot = SimpleNamespace()

        msg = SimpleNamespace(
            guild=None,
            channel=SimpleNamespace(id=1, send=AsyncMock()),
            author=SimpleNamespace(id=2, mention="<@2>"),
        )

        import asyncio

        asyncio.run(self.logger._log_fix_request(msg, "動いてない"))

        self.logger._extract_previous_turn_context.assert_awaited_once_with(msg)
        self.logger._decide_fix_mode.assert_awaited_once()
        self.logger._build_repair_user_reply.assert_awaited_once()

    def test_log_fix_request_includes_codex_branch_in_user_reply(self) -> None:
        self.logger._infer_fix_request_details = lambda _text: ("サーバー説明", "修正する")
        self.logger._extract_previous_turn_context = AsyncMock(return_value=("前回の質問", "前回の返答"))
        self.logger._decide_fix_mode = AsyncMock(
            return_value={
                "target_area": "サーバー説明",
                "planned_fix": "説明を修正する",
                "user_reply_hint": "確認します",
                "activate": True,
            }
        )
        self.logger._build_repair_user_reply = AsyncMock(return_value="確認します")
        self.logger._append_fix_request_to_rag = lambda **_kwargs: []
        self.logger._dispatch_codex_repair_logging = AsyncMock()
        self.logger._log_bot_activity_event = AsyncMock()
        self.logger._track_background_task = lambda _task: None
        self.logger.bot = SimpleNamespace()
        import asyncio

        monitor_task = object()
        self.logger._codex_job_manager = SimpleNamespace(
            is_available=lambda: True,
            start_job=AsyncMock(
                return_value=(
                    SimpleNamespace(job_id="job-1", branch_name="codex/serverinfo-job-1"),
                    monitor_task,
                )
            ),
        )

        msg = SimpleNamespace(
            guild=None,
            channel=SimpleNamespace(id=1, send=AsyncMock()),
            author=SimpleNamespace(id=2, mention="<@2>"),
        )

        asyncio.run(self.logger._log_fix_request(msg, "説明ちがう"))

        sent = msg.channel.send.await_args.args[0]
        self.assertIn("codex/serverinfo-job-1", sent)

    def test_append_fix_request_to_rag_writes_channel_scope(self) -> None:
        local_rag = SimpleNamespace(
            append_channel_qa=AsyncMock(),
            append_guild_qa=AsyncMock(),
        )
        local_rag.append_channel_qa = unittest.mock.Mock(return_value=Path("/tmp/channel/faq.json"))
        local_rag.append_guild_qa = unittest.mock.Mock(return_value=Path("/tmp/guild/faq.json"))
        self.logger._local_rag = local_rag
        self.logger._is_channel_profile_query = lambda _text: False

        msg = SimpleNamespace(
            id=10,
            guild=SimpleNamespace(id=20),
            channel=SimpleNamespace(id=30),
            author=SimpleNamespace(id=40, display_name="user", name="user"),
        )

        paths = self.logger._append_fix_request_to_rag(
            msg=msg,
            issue="この返答ちがう",
            target_area="応答品質",
            planned_fix="説明を修正する",
            previous_prompt="前の質問",
            previous_response="前の返答",
        )

        self.assertEqual(paths, ["/tmp/channel/faq.json"])
        local_rag.append_channel_qa.assert_called_once()
        local_rag.append_guild_qa.assert_not_called()

    def test_append_fix_request_to_rag_mirrors_place_fix_to_guild_scope(self) -> None:
        local_rag = SimpleNamespace(
            append_channel_qa=unittest.mock.Mock(return_value=Path("/tmp/channel/faq.json")),
            append_guild_qa=unittest.mock.Mock(return_value=Path("/tmp/guild/faq.json")),
        )
        self.logger._local_rag = local_rag
        self.logger._is_channel_profile_query = lambda _text: True

        msg = SimpleNamespace(
            id=10,
            guild=SimpleNamespace(id=20),
            channel=SimpleNamespace(id=30),
            author=SimpleNamespace(id=40, display_name="user", name="user"),
        )

        paths = self.logger._append_fix_request_to_rag(
            msg=msg,
            issue="このサーバー説明ちがう",
            target_area="サーバー説明",
            planned_fix="用途説明を修正する",
            previous_prompt="このサーバーは何するところ？",
            previous_response="交流の場です",
        )

        self.assertEqual(paths, ["/tmp/channel/faq.json", "/tmp/guild/faq.json"])
        local_rag.append_channel_qa.assert_called_once()
        local_rag.append_guild_qa.assert_called_once()

    def test_append_fix_request_to_rag_mirrors_authoritative_fix_to_guild_scope(self) -> None:
        local_rag = SimpleNamespace(
            append_channel_qa=unittest.mock.Mock(return_value=Path("/tmp/channel/faq.json")),
            append_guild_qa=unittest.mock.Mock(return_value=Path("/tmp/guild/faq.json")),
        )
        self.logger._local_rag = local_rag
        self.logger._is_channel_profile_query = lambda _text: False
        self.logger._cfg_int_list = lambda _key: [387651883847909376]

        msg = SimpleNamespace(
            id=10,
            guild=SimpleNamespace(id=20),
            channel=SimpleNamespace(id=30),
            author=SimpleNamespace(id=387651883847909376, display_name="admin", name="admin"),
        )

        paths = self.logger._append_fix_request_to_rag(
            msg=msg,
            issue="この返答ちがう",
            target_area="応答品質",
            planned_fix="説明を修正する",
            previous_prompt="前の質問",
            previous_response="前の返答",
        )

        self.assertEqual(paths, ["/tmp/channel/faq.json", "/tmp/guild/faq.json"])
        local_rag.append_channel_qa.assert_called_once()
        local_rag.append_guild_qa.assert_called_once()
        channel_kwargs = local_rag.append_channel_qa.call_args.kwargs
        self.assertTrue(channel_kwargs["metadata"]["authoritative_correction"])
        self.assertIn("authoritative_correction", channel_kwargs["tags"])


if __name__ == "__main__":
    unittest.main()
