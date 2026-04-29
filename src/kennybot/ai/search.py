"""Compatibility shim for legacy web search imports."""

from src.kennybot.features.search.service import (
    AISearchAnswer,
    AISearchService,
    DuckDuckGoSearch,
    SearchConfig,
    Summarizer,
    SummaryConfig,
    WebItem,
    WebSummarizer,
)

__all__ = [
    "AISearchAnswer",
    "AISearchService",
    "DuckDuckGoSearch",
    "SearchConfig",
    "Summarizer",
    "SummaryConfig",
    "WebItem",
    "WebSummarizer",
]
