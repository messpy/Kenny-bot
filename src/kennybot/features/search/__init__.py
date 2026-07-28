"""検索・RAG・外部情報取得機能。"""

from __future__ import annotations

from src.kennybot.features.search.live_info import ExternalContext, LiveInfoService
from src.kennybot.features.search.local_rag import LocalRAG, RagChunk
from src.kennybot.features.search.profile_preview import (
    build_channel_profile_preview,
    build_profile_chunks,
    format_profile_chunks,
    select_display_profile_chunks,
)
from src.kennybot.features.search.profile_preview_api import build_profile_preview_response, parse_json_payload
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
from src.kennybot.features.search.tool_api import build_tool_response, list_tool_menu


__all__ = [
    "AISearchAnswer",
    "AISearchService",
    "DuckDuckGoSearch",
    "SearchConfig",
    "Summarizer",
    "SummaryConfig",
    "WebItem",
    "WebSummarizer",
    "ExternalContext",
    "LiveInfoService",
    "LocalRAG",
    "RagChunk",
    "build_channel_profile_preview",
    "build_profile_chunks",
    "format_profile_chunks",
    "select_display_profile_chunks",
    "build_profile_preview_response",
    "parse_json_payload",
    "build_tool_response",
    "list_tool_menu",
]
