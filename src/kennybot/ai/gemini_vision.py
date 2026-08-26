from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import requests

from src.kennybot.ai.image_normalizer import ImageNormalizeError, normalize_image_for_vision


logger = logging.getLogger(__name__)


class GeminiVisionError(RuntimeError):
    """Raised when Gemini cannot produce an image analysis reply."""


@dataclass(frozen=True)
class GeminiVisionClient:
    api_key: str
    model: str = "gemini-3.7-flash"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: float = 120.0
    fallback_models: tuple[str, ...] = ()

    def analyze_images(
        self,
        *,
        prompt: str,
        images: list[tuple[bytes, str]],
        system_prompt: str,
    ) -> str:
        if not images:
            raise GeminiVisionError("image is required")

        parts: list[dict[str, Any]] = [{"text": f"{system_prompt}\n\n{prompt}".strip()}]
        for image_bytes, _mime_type in images:
            try:
                normalized_bytes, mime_type = normalize_image_for_vision(image_bytes)
            except ImageNormalizeError as exc:
                raise GeminiVisionError(str(exc)) from exc
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": base64.b64encode(normalized_bytes).decode("ascii"),
                    }
                }
            )

        last_error: GeminiVisionError | None = None
        for model in self._candidate_models():
            try:
                return self._post_parts(parts, model=model)
            except GeminiVisionError as exc:
                last_error = exc
                logger.warning("Gemini vision model failed: model=%s error=%s", model, exc)
        if last_error is not None:
            raise last_error
        raise GeminiVisionError("No Gemini model configured")

    def _candidate_models(self) -> list[str]:
        candidates: list[str] = []
        for model in (self.model, *self.fallback_models):
            value = str(model or "").strip()
            if value and value not in candidates:
                candidates.append(value)
        return candidates

    def _post_parts(self, parts: list[dict[str, Any]], *, model: str) -> str:
        model_name = model if model.startswith("models/") else f"models/{model}"
        try:
            response = requests.post(
                f"{self.base_url.rstrip('/')}/{model_name}:generateContent",
                headers={
                    "Content-Type": "application/json",
                    "X-goog-api-key": self.api_key,
                },
                json={"contents": [{"role": "user", "parts": parts}]},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            logger.warning("Gemini vision request failed: %s", exc)
            raise GeminiVisionError(str(exc)) from exc
        except ValueError as exc:
            raise GeminiVisionError("Gemini returned invalid JSON") from exc

        text = self._extract_text(data)
        if not text:
            raise GeminiVisionError("Gemini returned an empty reply")
        return text

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        candidates = response.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return ""
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            return ""
        texts = [
            str(part.get("text", "")).strip()
            for part in parts
            if isinstance(part, dict) and str(part.get("text", "")).strip()
        ]
        return "\n".join(texts).strip()
