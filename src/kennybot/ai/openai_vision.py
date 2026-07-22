from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import requests

from src.kennybot.ai.image_normalizer import ImageNormalizeError, normalize_image_for_vision


logger = logging.getLogger(__name__)


class OpenAIVisionError(RuntimeError):
    """Raised when OpenAI cannot produce an image analysis reply."""


@dataclass(frozen=True)
class OpenAIVisionClient:
    api_key: str
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 120.0

    def analyze_images(
        self,
        *,
        prompt: str,
        images: list[tuple[bytes, str]],
        system_prompt: str,
    ) -> str:
        if not images:
            raise OpenAIVisionError("image is required")

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_bytes, mime_type in images:
            try:
                normalized_bytes, normalized_mime = normalize_image_for_vision(image_bytes)
            except ImageNormalizeError as exc:
                raise OpenAIVisionError(str(exc)) from exc
            encoded = base64.b64encode(normalized_bytes).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{normalized_mime or mime_type};base64,{encoded}",
                    },
                }
            )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
        try:
            response = requests.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            logger.warning("OpenAI vision request failed: %s", exc)
            raise OpenAIVisionError(str(exc)) from exc
        except ValueError as exc:
            raise OpenAIVisionError("OpenAI returned invalid JSON") from exc

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise OpenAIVisionError("OpenAI returned no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content_text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content_text, str) or not content_text.strip():
            raise OpenAIVisionError("OpenAI returned an empty reply")
        return content_text.strip()


def detect_image_mime_type(image_bytes: bytes, fallback: str = "image/jpeg") -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"GIF87a") or image_bytes.startswith(b"GIF89a"):
        return "image/gif"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return fallback or "image/jpeg"
