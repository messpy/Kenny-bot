from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os

from src.kennybot.ai.client import OllamaClientService, create_ollama_client
from src.kennybot.ai.gemini_images import GeminiImageGenerationClient
from src.kennybot.ai.gemini_vision import GeminiVisionClient
from src.kennybot.ai.openai_images import OpenAIImageGenerationClient
from src.kennybot.ai.openai_vision import OpenAIVisionClient
from src.kennybot.ai.runner import OllamaConfig, OllamaRunner
from src.kennybot.features.chat import ChatConfig, ChatMemory, ChatService
from src.kennybot.features.search import (
    AISearchService,
    DuckDuckGoSearch,
    SearchConfig,
    SummaryConfig,
    WebSummarizer,
)
from src.kennybot.features.spam import SpamGuard, SpamPolicy
from src.kennybot.features.voice import MeetingMinutesManager
from src.kennybot.utils.ai_progress import AIProgressTracker
from src.kennybot.utils.app_settings import MAX_RESPONSE_LENGTH
from src.kennybot.utils.config import get_app_config
from src.kennybot.utils.runtime_settings import get_settings


logger = logging.getLogger(__name__)


@dataclass
class AppContainer:
    spam_guard: SpamGuard
    meeting_minutes: MeetingMinutesManager
    ai_progress_tracker: AIProgressTracker
    ollama_runner: OllamaRunner
    chat_memory: ChatMemory
    chat_service: ChatService
    ai_search: AISearchService | None
    ollama_client: OllamaClientService
    ollama_embed_client: OllamaClientService
    ollama_model: str
    openai_vision_client: OpenAIVisionClient | None = None
    openai_image_client: OpenAIImageGenerationClient | None = None
    gemini_vision_client: GeminiVisionClient | None = None
    gemini_image_client: GeminiImageGenerationClient | None = None


def resolve_ollama_host_for_runtime(host: str | None) -> str | None:
    value = (host or "").strip()
    if not value:
        return None
    lowered = value.lower()
    if "ollama.com" in lowered:
        return None
    return value


class ClientTextRunner:
    """Async text runner backed by OllamaClientService for Gemini/Ollama fallback."""

    def __init__(self, client: OllamaClientService) -> None:
        self.client = client

    async def run_async(self, prompt: str, *, model: str) -> str:
        result = await asyncio.to_thread(
            self.client.chat_simple,
            model=model,
            prompt=prompt,
        )
        if not result:
            raise RuntimeError(f"text generation returned empty response (model={model})")
        return result


def build_app_container() -> AppContainer:
    settings = get_settings()
    app_config = get_app_config()
    ai_models = app_config.ai_models()
    spam_config = app_config.spam()

    ai_concurrency = min(2, max(1, int(settings.get("security.ai_max_concurrency", 2))))
    spam_guard = SpamGuard(
        SpamPolicy(
            max_msgs=spam_config.max_msgs,
            per_seconds=spam_config.per_seconds,
            max_ai_calls=spam_config.max_ai_calls,
            ai_per_seconds=spam_config.ai_per_seconds,
            dup_window_seconds=spam_config.dup_window_seconds,
            warn_cooldown_seconds=spam_config.warn_cooldown_seconds,
        )
    )
    meeting_minutes = MeetingMinutesManager()
    ai_progress_tracker = AIProgressTracker(ai_concurrency)

    runner = OllamaRunner(
        OllamaConfig(model=ai_models.default, timeout_sec=ai_models.timeout_sec),
        debug=False,
    )
    chat_memory = ChatMemory(max_turns=10)
    chat_service = ChatService(
        runner=runner,
        config=ChatConfig(
            model=ai_models.chat,
            max_history_turns=10,
            max_output_chars=MAX_RESPONSE_LENGTH,
            concurrency=2,
        ),
        debug=False,
    )

    ollama_host = resolve_ollama_host_for_runtime(os.getenv("OLLAMA_HOST"))
    if ollama_host:
        logger.info("Using remote Ollama: %s", ollama_host)
        ollama_client = create_ollama_client(host=ollama_host)
    else:
        logger.info("Using local Ollama (http://localhost:11434)")
        ollama_client = create_ollama_client()
    ai_search_runner = ClientTextRunner(ollama_client)

    try:
        ai_search: AISearchService | None = AISearchService(
            searcher=DuckDuckGoSearch(
                SearchConfig(
                    top_n=3,
                    max_results=10,
                    timelimit="w",
                    region="jp-jp",
                    safesearch="moderate",
                    prefer_news=False,
                )
            ),
            summarizer=WebSummarizer(
                runner=ai_search_runner,
                config=SummaryConfig(
                    mode="normal",
                    concurrency=2,
                    model=ai_models.summary,
                    fallback_models=(ai_models.default,),
                    max_chars=400,
                ),
            ),
            runner=ai_search_runner,
            final_model=ai_models.summary,
            final_fallback_models=[ai_models.default],
            debug=False,
        )
    except Exception:
        logger.exception("Failed to initialize AI search service")
        ai_search = None

    openai_vision_client = None
    openai_image_client = None
    openai_api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if openai_api_key:
        openai_vision_client = OpenAIVisionClient(
            api_key=openai_api_key,
            model=os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "120")),
        )
        openai_image_client = OpenAIImageGenerationClient(
            api_key=openai_api_key,
            model=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            timeout_seconds=float(os.getenv("OPENAI_IMAGE_TIMEOUT_SECONDS", "180")),
        )

    gemini_vision_client = None
    gemini_image_client = None
    gemini_api_key = (
        os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
    ).strip()
    if gemini_api_key:
        gemini_vision_client = GeminiVisionClient(
            api_key=gemini_api_key,
            model=os.getenv("GEMINI_VISION_MODEL", "gemini-3.7-flash"),
            fallback_models=tuple(
                item.strip()
                for item in os.getenv(
                    "GEMINI_VISION_FALLBACK_MODELS",
                    "gemini-3.7-flash,gemini-2.5-flash,gemini-flash-latest,gemini-2.0-flash-lite",
                ).split(",")
                if item.strip()
            ),
            base_url=os.getenv(
                "GEMINI_API_BASE",
                "https://generativelanguage.googleapis.com/v1beta",
            ),
            timeout_seconds=float(os.getenv("GEMINI_TIMEOUT_SECONDS", "120")),
        )
        gemini_image_client = GeminiImageGenerationClient(
            api_key=gemini_api_key,
            model=os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
            base_url=os.getenv(
                "GEMINI_API_BASE",
                "https://generativelanguage.googleapis.com/v1beta",
            ),
            timeout_seconds=float(os.getenv("GEMINI_IMAGE_TIMEOUT_SECONDS", "180")),
        )

    ollama_embed_host = resolve_ollama_host_for_runtime(os.getenv("OLLAMA_EMBED_HOST"))
    if ollama_embed_host:
        logger.info("Using dedicated embed Ollama host: %s", ollama_embed_host)
        ollama_embed_client = create_ollama_client(host=ollama_embed_host)
    else:
        ollama_embed_client = ollama_client

    return AppContainer(
        spam_guard=spam_guard,
        meeting_minutes=meeting_minutes,
        ai_progress_tracker=ai_progress_tracker,
        ollama_runner=runner,
        chat_memory=chat_memory,
        chat_service=chat_service,
        ai_search=ai_search,
        ollama_client=ollama_client,
        ollama_embed_client=ollama_embed_client,
        ollama_model=ai_models.default,
        openai_vision_client=openai_vision_client,
        openai_image_client=openai_image_client,
        gemini_vision_client=gemini_vision_client,
        gemini_image_client=gemini_image_client,
    )
