from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from src.kennybot.utils.runtime_settings import get_settings

JST = timezone(timedelta(hours=9))
_settings = get_settings()

_WEATHER_CODES = {
    0: "快晴",
    1: "晴れ",
    2: "一部くもり",
    3: "くもり",
    45: "霧",
    48: "着氷性の霧",
    51: "弱い霧雨",
    53: "霧雨",
    55: "強い霧雨",
    56: "弱い着氷性の霧雨",
    57: "強い着氷性の霧雨",
    61: "弱い雨",
    63: "雨",
    65: "強い雨",
    66: "弱い着氷性の雨",
    67: "強い着氷性の雨",
    71: "弱い雪",
    73: "雪",
    75: "強い雪",
    77: "雪粒",
    80: "弱いにわか雨",
    81: "にわか雨",
    82: "強いにわか雨",
    85: "弱いにわか雪",
    86: "強いにわか雪",
    95: "雷雨",
    96: "弱いひょうを伴う雷雨",
    99: "強いひょうを伴う雷雨",
}

_WEEKDAYS_JA = ("月", "火", "水", "木", "金", "土", "日")


@dataclass(frozen=True)
class ExternalContext:
    label: str
    body: str


class LiveInfoService:
    def __init__(self) -> None:
        self._weather_timeout = float(_settings.get("external.weather_timeout_sec", 8))
        self._holiday_timeout = float(_settings.get("external.holiday_timeout_sec", 8))

    def needs_external_context(self, text: str) -> bool:
        lowered = (text or "").lower()
        return (
            self._looks_like_weather_query(lowered)
            or (self._looks_like_calendar_query(lowered) and not self._looks_like_news_query(lowered))
            or self._looks_like_season_query(lowered)
        )

    def build_context(self, text: str) -> list[ExternalContext]:
        contexts: list[ExternalContext] = []
        weather_query = self._looks_like_weather_query(text)
        news_query = self._looks_like_news_query(text)
        if weather_query:
            weather = self._fetch_weather_context(text)
            if weather:
                contexts.append(weather)

        if self._looks_like_calendar_query(text) and not weather_query and not news_query:
            calendar = self._build_calendar_context(text)
            if calendar:
                contexts.append(calendar)

        return contexts

    def _looks_like_weather_query(self, text: str) -> bool:
        lowered = (text or "").lower()
        weather_words = (
            "天気",
            "気温",
            "温度",
            "最高気温",
            "最低気温",
            "最高温度",
            "最低温度",
            "weather",
        )
        return any(word in lowered for word in weather_words)

    def _looks_like_calendar_query(self, text: str) -> bool:
        lowered = (text or "").lower()
        explicit_date = bool(re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", lowered))
        explicit_jp_date = bool(re.search(r"(\d{1,2})月(\d{1,2})日", lowered))
        date_words = any(key in lowered for key in ("今日", "明日", "明後日"))
        question_words = any(key in lowered for key in ("何日", "何曜日", "日付", "祝日", "休日"))
        return explicit_date or explicit_jp_date or question_words or (date_words and ("?" in lowered or "？" in lowered or question_words))

    def _looks_like_news_query(self, text: str) -> bool:
        lowered = (text or "").lower()
        news_words = (
            "ニュース",
            "news",
            "速報",
            "記事",
            "話題",
            "トレンド",
            "報道",
        )
        return any(word in lowered for word in news_words)

    def _looks_like_season_query(self, text: str) -> bool:
        lowered = (text or "").lower()
        season_words = (
            "季節",
            "時期",
            "旬",
            "今の",
            "この時期",
            "服装",
            "気候",
            "桜",
            "紅葉",
            "雪",
            "花粉",
            "熱中症",
        )
        return any(word in lowered for word in season_words)

    def _extract_weather_location(self, text: str) -> str:
        stripped = re.sub(r"<@!?\d+>", "", text or "").strip()
        patterns = (
            r"(.+?)の天気",
            r"(.+?)の気温",
            r"(.+?)の温度",
            r"(.+?)\s+weather",
            r"weather in\s+(.+)",
        )
        for pattern in patterns:
            m = re.search(pattern, stripped, re.IGNORECASE)
            if not m:
                continue
            value = m.group(1).strip(" 　?？!！,、。")
            if value:
                return value
        return str(_settings.get("external.weather_default_location", "Tokyo"))

    def _fetch_weather_context(self, text: str) -> ExternalContext | None:
        location = self._extract_weather_location(text)
        try:
            geo = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "ja", "format": "json"},
                timeout=self._weather_timeout,
            )
            geo.raise_for_status()
            geo_data = geo.json()
            results = geo_data.get("results") if isinstance(geo_data, dict) else None
            if not isinstance(results, list) or not results:
                return None

            place = results[0]
            lat = place.get("latitude")
            lon = place.get("longitude")
            if lat is None or lon is None:
                return None

            weather = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "forecast_days": 1,
                    "timezone": "Asia/Tokyo",
                },
                timeout=self._weather_timeout,
            )
            weather.raise_for_status()
            data = weather.json()
            current = data.get("current", {}) if isinstance(data, dict) else {}
            daily = data.get("daily", {}) if isinstance(data, dict) else {}

            resolved_name = str(place.get("name") or location)
            admin = str(place.get("admin1") or "")
            country = str(place.get("country") or "")
            place_label = " / ".join(part for part in (resolved_name, admin, country) if part)

            temp = current.get("temperature_2m")
            wind = current.get("wind_speed_10m")
            code = int(current.get("weather_code", -1))
            summary = _WEATHER_CODES.get(code, f"天気コード {code}")

            max_t = self._first_of(daily.get("temperature_2m_max"))
            min_t = self._first_of(daily.get("temperature_2m_min"))
            rain_prob = self._first_of(daily.get("precipitation_probability_max"))

            body = (
                f"地点: {place_label}\n"
                f"現在: {summary} / {temp}°C / 風速 {wind} km/h\n"
                f"今日: 最高 {max_t}°C / 最低 {min_t}°C / 降水確率 {rain_prob}%\n"
                f"取得元: Open-Meteo (https://open-meteo.com/)"
            )
            return ExternalContext("天気API", body)
        except Exception as e:
            return None

    def _build_calendar_context(self, text: str) -> ExternalContext | None:
        target = self._extract_target_date(text)
        holiday_name, holiday_error = self._fetch_holiday_name(target)
        weekday = _WEEKDAYS_JA[target.weekday()]
        lines = [
            f"日付: {target.isoformat()} ({weekday})",
            f"日本時間: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S JST')}",
        ]
        if holiday_name:
            lines.append(f"日本の祝日: {holiday_name}")
        else:
            lines.append("日本の祝日: 該当なし")
        if holiday_error:
            lines.append(f"祝日API備考: {holiday_error}")
        lines.append("取得元: Nager.Date (https://date.nager.at/)")
        return ExternalContext("日付・祝日API", "\n".join(lines))

    def _extract_target_date(self, text: str) -> date:
        now = datetime.now(JST).date()
        raw = text or ""
        if "明後日" in raw:
            return now + timedelta(days=2)
        if "明日" in raw:
            return now + timedelta(days=1)
        if "今日" in raw:
            return now

        m = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", raw)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return now

        m = re.search(r"(\d{1,2})月(\d{1,2})日", raw)
        if m:
            month = int(m.group(1))
            day = int(m.group(2))
            year = now.year
            try:
                candidate = date(year, month, day)
            except ValueError:
                return now
            if candidate < now - timedelta(days=180):
                try:
                    return date(year + 1, month, day)
                except ValueError:
                    return now
            return candidate
        return now

    def _fetch_holiday_name(self, target: date) -> tuple[str | None, str | None]:
        try:
            resp = requests.get(
                f"https://date.nager.at/api/v3/PublicHolidays/{target.year}/JP",
                timeout=self._holiday_timeout,
            )
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list):
                return None, "祝日APIの応答形式が不正です。"
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("date") == target.isoformat():
                    name = row.get("localName") or row.get("name")
                    if name:
                        return str(name), None
            return None, None
        except Exception as e:
            return None, str(e)[:180]

    @staticmethod
    def _first_of(value: Any) -> Any:
        if isinstance(value, list) and value:
            return value[0]
        return value
