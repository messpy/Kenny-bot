"""検索・RAG・外部情報取得機能。"""

from __future__ import annotations

from importlib import import_module


_EXPORTS = {
    "AISearchAnswer": "src.kennybot.features.search.service",
    "AISearchService": "src.kennybot.features.search.service",
    "DuckDuckGoSearch": "src.kennybot.features.search.service",
    "SearchConfig": "src.kennybot.features.search.service",
    "Summarizer": "src.kennybot.features.search.service",
    "SummaryConfig": "src.kennybot.features.search.service",
    "WebItem": "src.kennybot.features.search.service",
    "WebSummarizer": "src.kennybot.features.search.service",
    "ExternalContext": "src.kennybot.features.search.live_info",
    "LiveInfoService": "src.kennybot.features.search.live_info",
    "LocalRAG": "src.kennybot.features.search.local_rag",
    "RagChunk": "src.kennybot.features.search.local_rag",
    "build_channel_profile_preview": "src.kennybot.features.search.profile_preview",
    "build_profile_chunks": "src.kennybot.features.search.profile_preview",
    "format_profile_chunks": "src.kennybot.features.search.profile_preview",
    "select_display_profile_chunks": "src.kennybot.features.search.profile_preview",
    "build_profile_preview_response": "src.kennybot.features.search.profile_preview_api",
    "parse_json_payload": "src.kennybot.features.search.profile_preview_api",
    "build_tool_response": "src.kennybot.features.search.tool_api",
    "list_tool_menu": "src.kennybot.features.search.tool_api",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = import_module(module_name)
    return getattr(module, name)
