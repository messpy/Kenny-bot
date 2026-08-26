import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.kennybot.cogs.flag_quiz import (
    FlagQuiz,
    FlagQuizButton,
    FlagQuizView,
    MODE_COUNTRY_NAME,
    _QUIZ_MESSAGES,
    _USER_CHOICES,
    _answer_text_for_choice,
    _correct_country,
    _answer_stats_text,
    _normalize_quiz_content,
    _quiz_message_text,
    _quiz_text_reveals_answer,
    _replace_flag_appearance_quiz_text,
)


AI_CONTENT = {
    "choices": [
        {"emoji": "🇯🇵", "ja": "日本", "en": "Japan", "region": "Asia"},
        {"emoji": "🇨🇦", "ja": "カナダ", "en": "Canada", "region": "North America"},
        {"emoji": "🇧🇷", "ja": "ブラジル", "en": "Brazil", "region": "South America"},
        {"emoji": "🇰🇪", "ja": "ケニア", "en": "Kenya", "region": "Africa"},
    ],
    "correct_emoji": "🇯🇵",
    "quiz_text": "AIが作ったクイズ",
    "correct_response": "正解！🇯🇵\nAI豆知識\n🇨🇦 カナダ\n🇧🇷 ブラジル\n🇰🇪 ケニア",
    "hints": [],
}


class FlagQuizTests(unittest.TestCase):
    def setUp(self) -> None:
        _USER_CHOICES.clear()
        _QUIZ_MESSAGES.clear()

    def test_view_has_four_generated_flag_buttons(self) -> None:
        view = FlagQuizView(AI_CONTENT)

        self.assertEqual([item.label for item in view.children], ["🇯🇵", "🇨🇦", "🇧🇷", "🇰🇪"])
        self.assertTrue(all(item.custom_id.startswith("kennybot:flag_quiz:v2:") for item in view.children))

    def test_correct_button_uses_generated_response(self) -> None:
        button = FlagQuizButton(0, "🇯🇵", AI_CONTENT)
        interaction = SimpleNamespace(
            guild_id=123,
            channel_id=456,
            message=SimpleNamespace(id=789),
            user=SimpleNamespace(id=111),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        asyncio.run(button.callback(interaction))

        args, kwargs = interaction.response.send_message.await_args
        self.assertIn("正解！🇯🇵", args[0])
        self.assertIn("AI豆知識", args[0])
        self.assertTrue(kwargs["ephemeral"])

    def test_wrong_button_returns_answer_without_hints(self) -> None:
        button = FlagQuizButton(1, "🇨🇦", AI_CONTENT)
        interaction = SimpleNamespace(
            guild_id=123,
            channel_id=456,
            message=SimpleNamespace(id=789),
            user=SimpleNamespace(id=111),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        asyncio.run(button.callback(interaction))

        args, kwargs = interaction.response.send_message.await_args
        self.assertIn("残念！正解は 🇯🇵 日本 です。", args[0])
        self.assertNotIn("正解！🇯🇵", args[0])
        self.assertIn("AI豆知識", args[0])
        self.assertNotIn("ヒント", args[0])
        self.assertTrue(kwargs["ephemeral"])

    def test_button_allows_changed_answer_but_keeps_first_choice_for_stats(self) -> None:
        wrong_button = FlagQuizButton(1, "🇨🇦", AI_CONTENT)
        correct_button = FlagQuizButton(0, "🇯🇵", AI_CONTENT)
        interaction = SimpleNamespace(
            guild_id=123,
            channel_id=456,
            message=SimpleNamespace(id=789),
            user=SimpleNamespace(id=111),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        asyncio.run(wrong_button.callback(interaction))
        interaction.response.send_message.reset_mock()
        asyncio.run(correct_button.callback(interaction))

        args, kwargs = interaction.response.send_message.await_args
        self.assertIn("正解！🇯🇵", args[0])
        self.assertIn("集計は初回回答 🇨🇦 のままです", args[0])
        self.assertTrue(kwargs["ephemeral"])
        self.assertEqual(_USER_CHOICES[(789, 111)], "🇨🇦")

    def test_answer_text_for_wrong_choice_includes_correct_response(self) -> None:
        answer = _answer_text_for_choice(AI_CONTENT, "🇨🇦")

        self.assertIn("残念！正解は 🇯🇵 日本 です。", answer)
        self.assertNotIn("正解！🇯🇵", answer)
        self.assertIn("AI豆知識", answer)

    def test_answer_text_for_wrong_choice_includes_same_explanation_and_choices(self) -> None:
        answer = _answer_text_for_choice(AI_CONTENT, "🇨🇦")

        self.assertIn("AI豆知識", answer)
        self.assertIn("🇨🇦 カナダ", answer)
        self.assertIn("🇧🇷 ブラジル", answer)

    def test_answer_stats_count_first_answers_by_message(self) -> None:
        _USER_CHOICES[(789, 1)] = "🇯🇵"
        _USER_CHOICES[(789, 2)] = "🇨🇦"
        _USER_CHOICES[(789, 3)] = "🇨🇦"
        _USER_CHOICES[(789, 4)] = "🇧🇷"
        _USER_CHOICES[(999, 5)] = "🇯🇵"

        stats = _answer_stats_text(789, AI_CONTENT)

        self.assertIn("正解率: 4人中1人正解", stats)
        self.assertIn("解答者4人", stats)
        self.assertNotIn("🇯🇵1", stats)
        self.assertNotIn("🇨🇦2", stats)
        self.assertNotIn("🇧🇷1", stats)

    def test_quiz_message_text_includes_choice_country_names_and_stats(self) -> None:
        _USER_CHOICES[(789, 1)] = "🇯🇵"

        text = _quiz_message_text(AI_CONTENT, 789)

        self.assertIn("選択肢:", text)
        self.assertIn("🇯🇵 日本", text)
        self.assertIn("🇨🇦 カナダ", text)
        self.assertIn("正解率: 1人中1人正解", text)

    def test_flag_quiz_posts_ai_generated_quiz_with_resolved_country_map(self) -> None:
        class FakeClient:
            def __init__(self):
                self.kwargs = None

            def chat_simple(self, **_kwargs):
                self.kwargs = _kwargs
                return (
                    '{"choices":['
                    '{"emoji":"🇯🇵","ja":"日本","en":"Japan","region":"Asia"},'
                    '{"emoji":"🇨🇦","ja":"カナダ","en":"Canada","region":"North America"},'
                    '{"emoji":"🇧🇷","ja":"ブラジル","en":"Brazil","region":"South America"},'
                    '{"emoji":"🇰🇪","ja":"ケニア","en":"Kenya","region":"Africa"}],'
                    '"correct_emoji":"🇯🇵",'
                    '"quiz_text":"AIが作ったクイズ",'
                    '"correct_response":"正解！🇯🇵\\nAI豆知識\\n🇨🇦 カナダ\\n🇧🇷 ブラジル\\n🇰🇪 ケニア",'
                    '"hints":[]}'
                )

        client = FakeClient()
        bot = SimpleNamespace(
            add_view=lambda _view: None,
            ollama_client=client,
            ollama_model="test-model",
        )
        cog = FlagQuiz(bot)
        cog._resolve_country_map_urls = AsyncMock(return_value={"Japan": "https://map.example/japan.svg"})
        message = SimpleNamespace(id=789, add_reaction=AsyncMock())
        channel = SimpleNamespace(send=AsyncMock(return_value=message))
        interaction = SimpleNamespace(
            guild_id=123,
            channel_id=456,
            channel=channel,
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        asyncio.run(cog.flag_quiz.callback(cog, interaction))

        cog._resolve_country_map_urls.assert_awaited_once()
        interaction.response.defer.assert_awaited_once()
        channel.send.assert_awaited_once()
        send_args, send_kwargs = channel.send.await_args
        self.assertIn("AIが作ったクイズ", send_args[0])
        self.assertIn("選択肢:", send_args[0])
        self.assertIn("🇯🇵 日本", send_args[0])
        self.assertIn("世界地図: [アジア](<https://map.example/japan.svg>)", send_args[0])
        self.assertNotIn("位置図（日本）:", send_args[0])
        self.assertNotIn("view", send_kwargs)
        self.assertEqual(message.add_reaction.await_count, 4)
        self.assertEqual(_QUIZ_MESSAGES[789]["correct_emoji"], "🇯🇵")
        self.assertIn("国旗とは関係ない地理・文化・歴史・食・言語", client.kwargs["prompt"])
        self.assertIn("AI豆知識", _QUIZ_MESSAGES[789]["correct_response"])
        self.assertNotIn("[メッセージ](<https://map.example/japan.svg>)", _QUIZ_MESSAGES[789]["correct_response"])
        interaction.followup.send.assert_awaited_once_with("国旗クイズを投稿しました。", ephemeral=True)

    def test_reaction_add_sends_private_answer_and_updates_stats(self) -> None:
        bot_user = SimpleNamespace(id=9999)
        user = SimpleNamespace(send=AsyncMock())
        message = SimpleNamespace(id=789, edit=AsyncMock())
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        bot = SimpleNamespace(
            add_view=lambda _view: None,
            user=bot_user,
            get_channel=lambda _channel_id: channel,
            get_user=lambda _user_id: user,
        )
        cog = FlagQuiz(bot)
        _QUIZ_MESSAGES[789] = AI_CONTENT
        payload = SimpleNamespace(
            message_id=789,
            channel_id=456,
            user_id=111,
            emoji="🇨🇦",
            member=user,
        )

        asyncio.run(cog.on_raw_reaction_add(payload))

        user.send.assert_awaited_once()
        dm_args, _dm_kwargs = user.send.await_args
        self.assertIn("残念！正解は 🇯🇵 日本 です。", dm_args[0])
        self.assertNotIn("正解！🇯🇵", dm_args[0])
        self.assertEqual(_USER_CHOICES[(789, 111)], "🇨🇦")
        message.edit.assert_awaited_once()
        edit_kwargs = message.edit.await_args.kwargs
        self.assertIn("正解率: 1人中0人正解", edit_kwargs["content"])
        self.assertIn("解答者1人", edit_kwargs["content"])
        self.assertNotIn("🇨🇦1", edit_kwargs["content"])

    def test_reaction_add_uses_first_answer_for_stats_but_sends_current_answer(self) -> None:
        user = SimpleNamespace(send=AsyncMock())
        message = SimpleNamespace(id=789, edit=AsyncMock())
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        bot = SimpleNamespace(
            add_view=lambda _view: None,
            user=SimpleNamespace(id=9999),
            get_channel=lambda _channel_id: channel,
            get_user=lambda _user_id: user,
        )
        cog = FlagQuiz(bot)
        _QUIZ_MESSAGES[789] = AI_CONTENT
        _USER_CHOICES[(789, 111)] = "🇨🇦"
        payload = SimpleNamespace(
            message_id=789,
            channel_id=456,
            user_id=111,
            emoji="🇯🇵",
            member=user,
        )

        asyncio.run(cog.on_raw_reaction_add(payload))

        user.send.assert_awaited_once_with(AI_CONTENT["correct_response"])
        self.assertEqual(_USER_CHOICES[(789, 111)], "🇨🇦")
        edit_kwargs = message.edit.await_args.kwargs
        self.assertIn("正解率: 1人中0人正解", edit_kwargs["content"])

    def test_replaces_flag_appearance_quiz_text_with_trivia_text(self) -> None:
        content = _normalize_quiz_content(
            {
                **AI_CONTENT,
                "quiz_text": "青と白の横縞と左上に白十字が描かれている国はどれでしょうか？",
            },
            AI_CONTENT,
        )

        _replace_flag_appearance_quiz_text(content)

        self.assertIn("豆知識", content["quiz_text"])
        self.assertNotIn("横縞", content["quiz_text"])
        self.assertNotIn("十字", content["quiz_text"])

    def test_replaces_quiz_text_that_reveals_answer_but_keeps_choice_names(self) -> None:
        content = _normalize_quiz_content(
            {
                **AI_CONTENT,
                "quiz_text": "日本は寿司で知られています。どの国旗でしょう？",
            },
            AI_CONTENT,
        )

        self.assertTrue(_quiz_text_reveals_answer(content))
        _replace_flag_appearance_quiz_text(content)
        message_text = _quiz_message_text(content)

        quiz_part = message_text.split("選択肢:", 1)[0]
        choices_part = message_text.split("選択肢:", 1)[1]
        self.assertNotIn("日本", quiz_part)
        self.assertIn("🇯🇵 日本", choices_part)

    def test_generate_quiz_content_fallback_uses_resolved_country_map_in_quiz_text(self) -> None:
        bot = SimpleNamespace(
            add_view=lambda _view: None,
            ollama_client=None,
            ollama_model="test-model",
        )
        cog = FlagQuiz(bot)

        async def resolve_map(content):
            country = _correct_country(content)
            return {country["en"]: "https://map.example/country.svg"}

        cog._resolve_country_map_urls = AsyncMock(side_effect=resolve_map)

        content = asyncio.run(cog._generate_quiz_content())

        self.assertIn("世界地図: [", content["quiz_text"])
        self.assertIn("](<https://map.example/country.svg>)", content["quiz_text"])
        self.assertNotIn("https://map.example/country.svg", content["correct_response"])

    def test_country_name_mode_asks_country_name_and_hides_choice_country_names(self) -> None:
        bot = SimpleNamespace(
            add_view=lambda _view: None,
            ollama_client=None,
            ollama_model="test-model",
        )
        cog = FlagQuiz(bot)

        async def resolve_map(content):
            country = _correct_country(content)
            return {country["en"]: "https://map.example/country.svg"}

        cog._resolve_country_map_urls = AsyncMock(side_effect=resolve_map)

        content = asyncio.run(cog._generate_quiz_content(MODE_COUNTRY_NAME))
        country = _correct_country(content)
        message_text = _quiz_message_text(content)
        choices_part = message_text.split("選択肢:", 1)[1]

        self.assertIn(f"{country['ja']}の国旗はどれ", content["quiz_text"])
        self.assertIn("世界地図: [", content["quiz_text"])
        self.assertFalse(content["show_choice_country_names"])
        self.assertIn(country["emoji"], choices_part)
        self.assertNotIn(country["ja"], choices_part)


if __name__ == "__main__":
    unittest.main()
