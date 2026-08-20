from __future__ import annotations

import asyncio
import json
import random

import discord
from discord import app_commands
from discord.ext import commands

from src.kennybot.cogs.country_map_resolver import CountryMapResolver
from src.kennybot.utils.discord_markdown import DiscordMarkdown


_USER_CHOICES: dict[tuple[int, int], str] = {}
_QUIZ_MESSAGES: dict[int, dict[str, object]] = {}
_FLAG_APPEARANCE_TERMS = (
    "横縞",
    "縦縞",
    "縞",
    "ストライプ",
    "十字",
    "星",
    "月",
    "太陽",
    "円",
    "三角",
    "青",
    "白",
    "赤",
    "緑",
    "黄",
    "黒",
    "紋章",
    "描か",
    "左上",
    "右上",
)
_REGION_LABELS = {
    "Africa": "アフリカ",
    "Asia": "アジア",
    "Europe": "ヨーロッパ",
    "North America": "北アメリカ",
    "Oceania": "オセアニア",
    "South America": "南アメリカ",
}
MODE_TRIVIA = "trivia"
MODE_COUNTRY_NAME = "country_name"

COUNTRY_POOL = (
    {"emoji": "🇦🇷", "ja": "アルゼンチン", "en": "Argentina", "region": "South America"},
    {"emoji": "🇦🇺", "ja": "オーストラリア", "en": "Australia", "region": "Oceania"},
    {"emoji": "🇧🇩", "ja": "バングラデシュ", "en": "Bangladesh", "region": "Asia"},
    {"emoji": "🇧🇹", "ja": "ブータン", "en": "Bhutan", "region": "Asia"},
    {"emoji": "🇧🇷", "ja": "ブラジル", "en": "Brazil", "region": "South America"},
    {"emoji": "🇨🇦", "ja": "カナダ", "en": "Canada", "region": "North America"},
    {"emoji": "🇨🇭", "ja": "スイス", "en": "Switzerland", "region": "Europe"},
    {"emoji": "🇨🇱", "ja": "チリ", "en": "Chile", "region": "South America"},
    {"emoji": "🇨🇴", "ja": "コロンビア", "en": "Colombia", "region": "South America"},
    {"emoji": "🇩🇰", "ja": "デンマーク", "en": "Denmark", "region": "Europe"},
    {"emoji": "🇪🇬", "ja": "エジプト", "en": "Egypt", "region": "Africa"},
    {"emoji": "🇪🇸", "ja": "スペイン", "en": "Spain", "region": "Europe"},
    {"emoji": "🇫🇮", "ja": "フィンランド", "en": "Finland", "region": "Europe"},
    {"emoji": "🇬🇷", "ja": "ギリシャ", "en": "Greece", "region": "Europe"},
    {"emoji": "🇮🇩", "ja": "インドネシア", "en": "Indonesia", "region": "Asia"},
    {"emoji": "🇮🇪", "ja": "アイルランド", "en": "Ireland", "region": "Europe"},
    {"emoji": "🇮🇳", "ja": "インド", "en": "India", "region": "Asia"},
    {"emoji": "🇮🇸", "ja": "アイスランド", "en": "Iceland", "region": "Europe"},
    {"emoji": "🇯🇲", "ja": "ジャマイカ", "en": "Jamaica", "region": "North America"},
    {"emoji": "🇯🇵", "ja": "日本", "en": "Japan", "region": "Asia"},
    {"emoji": "🇰🇪", "ja": "ケニア", "en": "Kenya", "region": "Africa"},
    {"emoji": "🇰🇷", "ja": "韓国", "en": "South Korea", "region": "Asia"},
    {"emoji": "🇱🇰", "ja": "スリランカ", "en": "Sri Lanka", "region": "Asia"},
    {"emoji": "🇲🇦", "ja": "モロッコ", "en": "Morocco", "region": "Africa"},
    {"emoji": "🇲🇳", "ja": "モンゴル", "en": "Mongolia", "region": "Asia"},
    {"emoji": "🇲🇽", "ja": "メキシコ", "en": "Mexico", "region": "North America"},
    {"emoji": "🇳🇵", "ja": "ネパール", "en": "Nepal", "region": "Asia"},
    {"emoji": "🇳🇿", "ja": "ニュージーランド", "en": "New Zealand", "region": "Oceania"},
    {"emoji": "🇵🇪", "ja": "ペルー", "en": "Peru", "region": "South America"},
    {"emoji": "🇵🇭", "ja": "フィリピン", "en": "Philippines", "region": "Asia"},
    {"emoji": "🇵🇹", "ja": "ポルトガル", "en": "Portugal", "region": "Europe"},
    {"emoji": "🇸🇪", "ja": "スウェーデン", "en": "Sweden", "region": "Europe"},
    {"emoji": "🇹🇭", "ja": "タイ", "en": "Thailand", "region": "Asia"},
    {"emoji": "🇹🇷", "ja": "トルコ", "en": "Turkey", "region": "Asia"},
    {"emoji": "🇹🇲", "ja": "トルクメニスタン", "en": "Turkmenistan", "region": "Asia"},
    {"emoji": "🇺🇾", "ja": "ウルグアイ", "en": "Uruguay", "region": "South America"},
    {"emoji": "🇻🇳", "ja": "ベトナム", "en": "Vietnam", "region": "Asia"},
    {"emoji": "🇿🇦", "ja": "南アフリカ", "en": "South Africa", "region": "Africa"},
)
COUNTRY_BY_EMOJI = {str(country["emoji"]): country for country in COUNTRY_POOL}
CORRECT_EMOJI = "🇧🇹"
CHOICES = ("🇱🇰", "🇧🇹", "🇲🇳", "🇹🇲")
COUNTRY_OPTIONS = {emoji: COUNTRY_BY_EMOJI[emoji] for emoji in CHOICES}
CORRECT_COUNTRY = COUNTRY_BY_EMOJI[CORRECT_EMOJI]
TRIVIA_COUNTRY = CORRECT_COUNTRY


def _clean_country(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    emoji = str(raw.get("emoji") or "").strip()
    if emoji in COUNTRY_BY_EMOJI:
        return dict(COUNTRY_BY_EMOJI[emoji])
    ja = str(raw.get("ja") or "").strip()
    en = str(raw.get("en") or "").strip()
    region = str(raw.get("region") or "").strip()
    if not emoji or not ja or not en:
        return None
    return {"emoji": emoji, "ja": ja, "en": en, "region": region}


def _fallback_quiz_content(
    map_urls: dict[str, str | None] | None = None,
    *,
    mode: str = MODE_TRIVIA,
) -> dict[str, object]:
    choices = [dict(country) for country in random.sample(COUNTRY_POOL, 4)]
    correct = random.choice(choices)
    other_lines = "\n".join(
        f"{country['emoji']} {country['ja']}" for country in choices if country["emoji"] != correct["emoji"]
    )
    content = {
        "choices": choices,
        "correct_emoji": correct["emoji"],
        "quiz_text": _fallback_trivia_quiz_text(correct),
        "question_mode": MODE_TRIVIA,
        "show_choice_country_names": True,
        "correct_response": (
            f"正解！{correct['emoji']}\n"
            f"{correct['ja']}（{correct['en']}）でした。\n"
            f"豆知識: この国は{correct['region']}にあり、地域の文化や歴史にも特色があります。\n\n"
            f"ほかの選択肢:\n{other_lines}"
        ),
        "hints": [],
    }
    _apply_question_mode(content, mode)
    content["quiz_text"] = _quiz_text_with_map_url(
        content,
        map_urls,
        str(content["quiz_text"]),
    )
    return content


def _fallback_trivia_quiz_text(country: dict[str, str]) -> str:
    return (
        "突然ですが国旗クイズ！次の豆知識にあてはまる国の国旗はどれでしょう？\n"
        f"豆知識: この国は{country['region']}にあり、独自の文化や歴史で知られています。\n"
        "これ！と思う国旗を選んでね。"
    )


def _fallback_country_name_quiz_text(country: dict[str, str]) -> str:
    return (
        f"突然ですが国旗クイズ！{country['ja']}の国旗はどれでしょう？\n"
        "これ！と思う国旗を選んでね。"
    )


def _extract_json_object(text: str) -> dict[str, object]:
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        parsed = json.loads(raw[start : end + 1])
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_hints(raw_hints: object, fallback: dict[str, object]) -> list[str]:
    hints = [str(item).strip() for item in raw_hints] if isinstance(raw_hints, list) else []
    hints = [hint for hint in hints if hint]
    if len(hints) < 3:
        return list(fallback["hints"])
    return hints[:3]


def _normalize_quiz_content(
    payload: dict[str, object],
    fallback: dict[str, object] | None = None,
) -> dict[str, object]:
    fallback = fallback or _fallback_quiz_content()
    raw_choices = payload.get("choices")
    choices = [_clean_country(item) for item in raw_choices] if isinstance(raw_choices, list) else []
    choices = [choice for choice in choices if choice is not None]
    seen: set[str] = set()
    unique_choices: list[dict[str, str]] = []
    for choice in choices:
        emoji = str(choice["emoji"])
        if emoji in seen:
            continue
        seen.add(emoji)
        unique_choices.append(choice)
    if len(unique_choices) != 4:
        unique_choices = [dict(country) for country in fallback["choices"]]  # type: ignore[index]

    correct_emoji = str(payload.get("correct_emoji") or fallback["correct_emoji"]).strip()
    choice_emojis = {str(choice["emoji"]) for choice in unique_choices}
    if correct_emoji not in choice_emojis:
        correct_emoji = str(fallback["correct_emoji"])
        if correct_emoji not in choice_emojis:
            correct_emoji = str(unique_choices[0]["emoji"])

    quiz_text = str(payload.get("quiz_text") or fallback["quiz_text"]).strip()
    correct_response = str(payload.get("correct_response") or fallback["correct_response"]).strip()
    hints = _normalize_hints(payload.get("hints"), fallback)
    return {
        "choices": unique_choices,
        "correct_emoji": correct_emoji,
        "quiz_text": quiz_text[:1900] or fallback["quiz_text"],
        "correct_response": correct_response[:1900] or fallback["correct_response"],
        "hints": hints,
        "question_mode": str(payload.get("question_mode") or fallback.get("question_mode") or MODE_TRIVIA),
        "show_choice_country_names": bool(
            payload.get("show_choice_country_names", fallback.get("show_choice_country_names", True))
        ),
    }


def _correct_country(content: dict[str, object]) -> dict[str, str]:
    correct_emoji = str(content.get("correct_emoji") or "")
    choices = content.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            country = _clean_country(choice)
            if country and country["emoji"] == correct_emoji:
                return country
    return dict(CORRECT_COUNTRY)


def _quiz_text_with_map_url(
    content: dict[str, object],
    map_urls: dict[str, str | None] | None = None,
    quiz_text: str | None = None,
) -> str:
    text = (quiz_text or str(content.get("quiz_text") or "")).strip()
    country = _correct_country(content)
    map_url = (map_urls or {}).get(str(country["en"]))
    if not map_url or map_url in text:
        return text
    region_label = _REGION_LABELS.get(str(country.get("region") or ""), str(country.get("region") or "世界地図"))
    map_link = DiscordMarkdown.link(region_label, map_url)
    return f"{text}\n\n世界地図: {map_link}"


def _choice_lines(content: dict[str, object]) -> list[str]:
    choices = content.get("choices")
    if not isinstance(choices, list):
        return []
    lines: list[str] = []
    for choice in choices:
        country = _clean_country(choice)
        if country is None:
            continue
        if bool(content.get("show_choice_country_names", True)):
            lines.append(f"{country['emoji']} {country['ja']}")
        else:
            lines.append(country["emoji"])
    return lines


def _quiz_text_with_choices(content: dict[str, object]) -> str:
    text = str(content.get("quiz_text") or "").strip()
    choice_lines = _choice_lines(content)
    if not choice_lines:
        return text
    return f"{text}\n\n選択肢:\n" + "\n".join(choice_lines)


def _answer_stats_text(message_id: int, content: dict[str, object]) -> str:
    choices = content.get("choices")
    if not isinstance(choices, list):
        return ""
    answers = [
        emoji
        for (stored_message_id, _user_id), emoji in _USER_CHOICES.items()
        if stored_message_id == message_id
    ]
    if not answers:
        return "正解率: 0人中0人正解\n解答者0人"

    correct_emoji = str(content.get("correct_emoji") or "")
    correct_count = sum(1 for emoji in answers if emoji == correct_emoji)
    return f"正解率: {len(answers)}人中{correct_count}人正解\n解答者{len(answers)}人"


def _quiz_message_text(content: dict[str, object], message_id: int | None = None) -> str:
    text = _quiz_text_with_choices(content)
    if message_id is None:
        return text
    return f"{text}\n\n{_answer_stats_text(message_id, content)}"


def _choice_emojis(content: dict[str, object]) -> set[str]:
    return set(_choice_emoji_list(content))


def _choice_emoji_list(content: dict[str, object]) -> list[str]:
    choices = content.get("choices")
    if not isinstance(choices, list):
        return []
    emojis: list[str] = []
    for choice in choices:
        country = _clean_country(choice)
        if country is not None and country["emoji"] not in emojis:
            emojis.append(country["emoji"])
    return emojis


def _answer_text_for_choice(content: dict[str, object], emoji: str) -> str:
    if emoji == str(content.get("correct_emoji") or ""):
        return str(content["correct_response"])
    country = _correct_country(content)
    explanation = str(content["correct_response"]).strip()
    correct_prefix = f"正解！{country['emoji']}"
    if explanation.startswith(correct_prefix):
        explanation = explanation[len(correct_prefix) :].lstrip()
    return f"残念！正解は {country['emoji']} {country['ja']} です。\n\n{explanation}"


def _uses_flag_appearance_clue(text: str) -> bool:
    return any(term in text for term in _FLAG_APPEARANCE_TERMS)


def _quiz_text_reveals_answer(content: dict[str, object]) -> bool:
    if str(content.get("question_mode") or MODE_TRIVIA) == MODE_COUNTRY_NAME:
        return False
    quiz_text = str(content.get("quiz_text") or "").casefold()
    country = _correct_country(content)
    names = (
        str(country.get("ja") or "").strip().casefold(),
        str(country.get("en") or "").strip().casefold(),
    )
    return any(name and name in quiz_text for name in names)


def _replace_flag_appearance_quiz_text(content: dict[str, object]) -> None:
    quiz_text = str(content.get("quiz_text") or "")
    if not _uses_flag_appearance_clue(quiz_text) and not _quiz_text_reveals_answer(content):
        return
    content["quiz_text"] = _fallback_trivia_quiz_text(_correct_country(content))


def _apply_question_mode(content: dict[str, object], mode: str) -> None:
    normalized_mode = MODE_COUNTRY_NAME if mode == MODE_COUNTRY_NAME else MODE_TRIVIA
    content["question_mode"] = normalized_mode
    if normalized_mode == MODE_COUNTRY_NAME:
        content["quiz_text"] = _fallback_country_name_quiz_text(_correct_country(content))
        content["show_choice_country_names"] = False
        return
    content["show_choice_country_names"] = True


class FlagQuizView(discord.ui.View):
    def __init__(self, content: dict[str, object] | None = None) -> None:
        super().__init__(timeout=None)
        self.content = _normalize_quiz_content(content or {})
        choices = self.content["choices"]
        assert isinstance(choices, list)
        for index, choice in enumerate(choices):
            country = _clean_country(choice)
            if country is None:
                continue
            self.add_item(FlagQuizButton(index, country["emoji"], self.content))


class FlagQuizButton(discord.ui.Button):
    def __init__(self, index: int, emoji: str, content: dict[str, object] | None = None) -> None:
        super().__init__(
            label=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"kennybot:flag_quiz:v2:{index}",
        )
        self.flag_emoji = emoji
        self.content = _normalize_quiz_content(content or {})

    def _stored_choice_key(self, interaction: discord.Interaction) -> tuple[int, int] | None:
        message = getattr(interaction, "message", None)
        user = getattr(interaction, "user", None)
        message_id = int(getattr(message, "id", 0) or 0)
        user_id = int(getattr(user, "id", 0) or 0)
        if not message_id or not user_id:
            return None
        return (message_id, user_id)

    def _response_for_choice(self, emoji: str) -> str:
        return _answer_text_for_choice(self.content, emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        choice_key = self._stored_choice_key(interaction)
        stored_emoji = _USER_CHOICES.get(choice_key) if choice_key is not None else None
        if choice_key is not None:
            _USER_CHOICES.setdefault(choice_key, self.flag_emoji)
        response = self._response_for_choice(self.flag_emoji)
        if stored_emoji is not None and stored_emoji != self.flag_emoji:
            response = f"{response}\n\n集計は初回回答 {stored_emoji} のままです。"
        await interaction.response.send_message(response, ephemeral=True)
        await self._update_stats_message(interaction)

    async def _update_stats_message(self, interaction: discord.Interaction) -> None:
        message = getattr(interaction, "message", None)
        message_id = int(getattr(message, "id", 0) or 0)
        if not message_id or message is None or not hasattr(message, "edit"):
            return
        try:
            await message.edit(content=_quiz_message_text(self.content, message_id))
        except Exception:
            return


class FlagQuiz(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        bot.add_view(FlagQuizView())

    async def _resolve_country_map_urls(self, content: dict[str, object] | None = None) -> dict[str, str | None]:
        normalized = _normalize_quiz_content(content or {})
        country = _correct_country(normalized)
        try:
            async with CountryMapResolver() as resolver:
                return {
                    country["en"]: await resolver.get_map_url(
                        country["en"],
                        region_name=str(country.get("region") or ""),
                    )
                }
        except Exception:
            return {}

    def _country_pool_context_for_prompt(self) -> str:
        sample_size = min(18, len(COUNTRY_POOL))
        countries = random.sample(COUNTRY_POOL, sample_size)
        return "\n".join(
            f"- {country['emoji']} {country['ja']} / {country['en']} / {country['region']}"
            for country in countries
        )

    async def _generate_quiz_content(self, mode: str = MODE_TRIVIA) -> dict[str, object]:
        mode = MODE_COUNTRY_NAME if mode == MODE_COUNTRY_NAME else MODE_TRIVIA
        fallback = _fallback_quiz_content(mode=mode)
        prompt = (
            "Discordの国旗クイズを日本語で1問作ってください。\n"
            "下の候補国から正解1つと不正解3つを選び、毎回なるべく違う国・違う地域・違う難易度にしてください。\n"
            f"{self._country_pool_context_for_prompt()}\n"
            "返すJSONのchoicesは必ず4件で、各要素はemoji/ja/en/regionを含めてください。\n"
            "correct_emojiはchoices内の正解のemojiにしてください。\n"
            "quiz_textは国旗の見た目ではなく、国旗とは関係ない地理・文化・歴史・食・言語などの豆知識から国を当てる問題にしてください。\n"
            "quiz_textでは正解国名、正解国の英語名、国旗の色、縞、十字、星、紋章、形、配置などを絶対に説明しないでください。\n"
            "国名はchoicesにだけ入れてください。quiz_text本文には正解国名を出さないでください。\n"
            "correct_responseには「正解！<emoji>」、正解国名、国旗とは関係ない短い豆知識、ほかの選択肢の国名を含めてください。\n"
            "位置図URLはこちらで問題文側に付けるため、JSON内のquiz_text/correct_responseには含めないでください。\n"
            "JSONだけを返してください。\n"
            '{"choices":[{"emoji":"...","ja":"...","en":"...","region":"..."}],'
            '"correct_emoji":"...","quiz_text":"...","correct_response":"...",'
            '"hints":[]}'
        )
        client = getattr(self.bot, "ollama_client", None)
        if client is None or not hasattr(client, "chat_simple"):
            map_urls = await self._resolve_country_map_urls(fallback)
            fallback["quiz_text"] = _quiz_text_with_map_url(
                fallback,
                map_urls,
                str(fallback["quiz_text"]),
            )
            return fallback
        model = str(getattr(self.bot, "ollama_model", "") or "gemini-3.7-flash")
        try:
            raw = await asyncio.to_thread(
                client.chat_simple,
                model=model,
                prompt=prompt,
                stream=False,
                format="json",
            )
        except Exception:
            map_urls = await self._resolve_country_map_urls(fallback)
            fallback["quiz_text"] = _quiz_text_with_map_url(
                fallback,
                map_urls,
                str(fallback["quiz_text"]),
            )
            return fallback
        content = _normalize_quiz_content(_extract_json_object(str(raw or "")), fallback)
        _apply_question_mode(content, mode)
        if mode == MODE_TRIVIA:
            _replace_flag_appearance_quiz_text(content)
        map_urls = await self._resolve_country_map_urls(content)
        content["quiz_text"] = _quiz_text_with_map_url(
            content,
            map_urls,
            str(content["quiz_text"]),
        )
        return content

    @app_commands.command(name="flag_quiz", description="現在のチャンネルに国旗クイズを投稿します")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="豆知識から国旗", value=MODE_TRIVIA),
            app_commands.Choice(name="国名から国旗", value=MODE_COUNTRY_NAME),
        ]
    )
    async def flag_quiz(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str] | None = None,
    ) -> None:
        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            await interaction.response.send_message("投稿先チャンネルを取得できませんでした。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        selected_mode = mode.value if mode is not None else MODE_TRIVIA
        content = await self._generate_quiz_content(selected_mode)
        message = await channel.send(_quiz_message_text(content))
        message_id = int(getattr(message, "id", 0) or 0)
        if message_id:
            _QUIZ_MESSAGES[message_id] = content
        for emoji in _choice_emoji_list(content):
            try:
                await message.add_reaction(emoji)
            except Exception:
                continue
        await interaction.followup.send("国旗クイズを投稿しました。", ephemeral=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if getattr(payload, "user_id", None) == getattr(getattr(self.bot, "user", None), "id", None):
            return
        message_id = int(getattr(payload, "message_id", 0) or 0)
        content = _QUIZ_MESSAGES.get(message_id)
        if content is None:
            return
        emoji = str(getattr(payload, "emoji", "") or "")
        if emoji not in _choice_emojis(content):
            return
        user_id = int(getattr(payload, "user_id", 0) or 0)
        if user_id:
            _USER_CHOICES.setdefault((message_id, user_id), emoji)
        await self._send_private_answer(payload, content, emoji)
        await self._update_quiz_message(payload, content)

    async def _send_private_answer(
        self,
        payload: discord.RawReactionActionEvent,
        content: dict[str, object],
        emoji: str,
    ) -> None:
        user = getattr(payload, "member", None)
        if user is None:
            user_id = int(getattr(payload, "user_id", 0) or 0)
            user = self.bot.get_user(user_id) if user_id else None
        if user is None:
            return
        try:
            await user.send(_answer_text_for_choice(content, emoji))
        except Exception:
            return

    async def _update_quiz_message(
        self,
        payload: discord.RawReactionActionEvent,
        content: dict[str, object],
    ) -> None:
        channel = self.bot.get_channel(int(getattr(payload, "channel_id", 0) or 0))
        if channel is None or not hasattr(channel, "fetch_message"):
            return
        try:
            message = await channel.fetch_message(int(getattr(payload, "message_id", 0) or 0))
            await message.edit(content=_quiz_message_text(content, int(message.id)))
        except Exception:
            return
