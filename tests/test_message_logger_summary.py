import sys
from pathlib import Path
import unittest
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock
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
    discord.abc.Messageable = object
    ext = types.ModuleType("discord.ext")
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


class MessageLoggerSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = MessageLogger.__new__(MessageLogger)

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
        self.assertFalse(self.logger._is_person_lookup_query("このサーバーはなにするところ？"))

    def test_channel_profile_query_detection(self) -> None:
        self.assertTrue(self.logger._is_channel_profile_query("このサーバーは何のやつ？"))
        self.assertTrue(self.logger._is_channel_profile_query("このサーバーってなにをするところ？"))
        self.assertTrue(self.logger._is_channel_profile_query("このサーバの情報を教えて"))
        self.assertTrue(self.logger._is_channel_profile_query("サーバの説明を教えて"))
        self.assertFalse(self.logger._is_channel_profile_query("このBotの機能を教えて"))

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

        context, refs, web_queries, details = asyncio.run(
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
        self.logger._resolve_chat_context = AsyncMock(return_value=("context", [], [], []))
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


if __name__ == "__main__":
    unittest.main()
