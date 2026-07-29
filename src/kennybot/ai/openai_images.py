from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

import requests


logger = logging.getLogger(__name__)


class OpenAIImageGenerationError(RuntimeError):
    """Raised when OpenAI cannot generate an image."""


@dataclass(frozen=True)
class OpenAIImageGenerationClient:
    api_key: str
    model: str = "gpt-image-1"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 180.0

    def generate_png(self, *, prompt: str, size: str = "1024x1024", model: str | None = None) -> bytes:
        prompt = (prompt or "").strip()
        if not prompt:
            raise OpenAIImageGenerationError("prompt is required")

        payload = {
            "model": (model or self.model).strip() or self.model,
            "prompt": prompt,
            "size": size,
            "n": 1,
        }
        try:
            response = requests.post(
                f"{self.base_url.rstrip('/')}/images/generations",
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
            logger.warning("OpenAI image generation request failed: %s", exc)
            raise OpenAIImageGenerationError(str(exc)) from exc
        except ValueError as exc:
            raise OpenAIImageGenerationError("OpenAI returned invalid JSON") from exc

        items = data.get("data")
        if not isinstance(items, list) or not items:
            raise OpenAIImageGenerationError("OpenAI returned no image data")
        first = items[0] if isinstance(items[0], dict) else {}
        encoded = first.get("b64_json")
        if isinstance(encoded, str) and encoded.strip():
            try:
                return base64.b64decode(encoded)
            except Exception as exc:
                raise OpenAIImageGenerationError("OpenAI returned invalid image data") from exc

        url = first.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            try:
                image_response = requests.get(url, timeout=self.timeout_seconds)
                image_response.raise_for_status()
                return image_response.content
            except requests.RequestException as exc:
                raise OpenAIImageGenerationError(str(exc)) from exc

        raise OpenAIImageGenerationError("OpenAI returned no usable image")
