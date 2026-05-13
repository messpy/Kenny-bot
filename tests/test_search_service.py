from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from src.kennybot.features.search import service
from src.kennybot.features.search.service import DuckDuckGoSearch, SearchConfig


class DuckDuckGoSearchTest(TestCase):
    def test_news_error_falls_back_to_text_search_when_news_only(self) -> None:
        class FakeDDGS:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def news(self, *args, **kwargs):
                raise service.DDGSException("https://duckduckgo.com/news.js 403 Forbidden")

            def text(self, *args, **kwargs):
                return iter(
                    [
                        {
                            "title": "Fallback result",
                            "href": "https://example.com/fallback",
                            "body": "text search worked",
                        }
                    ]
                )

        searcher = DuckDuckGoSearch(SearchConfig(max_results=3, prefer_news=True))

        with patch.object(service, "DDGS", FakeDDGS):
            items = searcher.search("今日の主要な技術ニュース", news_only=True)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "web")
        self.assertEqual(items[0].url, "https://example.com/fallback")
