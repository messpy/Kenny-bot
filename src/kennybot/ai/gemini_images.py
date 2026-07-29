from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import requests


logger = logging.getLogger(__name__)


class GeminiImageGenerationError(RuntimeError):
    """Raised when Gemini cannot generate an image."""


class GeminiImageRateLimitError(GeminiImageGenerationError):
    """Raised when Gemini image generation is rate limited or quota capped."""


def _sanitize_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if not parts.query:
            return value
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "<redacted>", parts.fragment))
    except Exception:
        return value.replace("key=", "key=<redacted>")


def _http_error_message(exc: requests.RequestException) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    body = ""
    try:
        body = response.text[:500]
    except Exception:
        body = ""
    return f"{response.status_code} {response.reason} for {_sanitize_url(response.url)} {body}".strip()


@dataclass(frozen=True)
class GeminiImageGenerationResult:
    data: bytes
    mime_type: str

    @property
    def filename(self) -> str:
        if self.mime_type == "image/jpeg":
            return "ai-generated.jpg"
        if self.mime_type == "image/webp":
            return "ai-generated.webp"
        return "ai-generated.png"


@dataclass(frozen=True)
class GeminiImageGenerationClient:
    api_key: str
    model: str = "gemini-2.5-flash-image"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: float = 180.0

    def generate_image(self, *, prompt: str, model: str | None = None) -> GeminiImageGenerationResult:
        prompt = (prompt or "").strip()
        if not prompt:
            raise GeminiImageGenerationError("prompt is required")
        model_name = (model or self.model).strip() or self.model
        if model_name.startswith("models/"):
            model_name = model_name.split("/", 1)[1]

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
        }
        try:
            response = requests.post(
                f"{self.base_url.rstrip('/')}/models/{model_name}:generateContent",
                params={"key": self.api_key},
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            message = _http_error_message(exc)
            logger.warning("Gemini image generation request failed: %s", message)
            response = getattr(exc, "response", None)
            if response is not None and response.status_code == 429:
                raise GeminiImageRateLimitError(message) from exc
            raise GeminiImageGenerationError(message) from exc
        except ValueError as exc:
            raise GeminiImageGenerationError("Gemini returned invalid JSON") from exc

        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise GeminiImageGenerationError("Gemini returned no candidates")
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise GeminiImageGenerationError("Gemini returned no parts")

        text_parts: list[str] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline_data = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline_data, dict):
                encoded = inline_data.get("data")
                mime_type = str(inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png")
                if isinstance(encoded, str) and encoded.strip():
                    try:
                        return GeminiImageGenerationResult(base64.b64decode(encoded), mime_type)
                    except Exception as exc:
                        raise GeminiImageGenerationError("Gemini returned invalid image data") from exc
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())

        detail = f": {' '.join(text_parts)[:240]}" if text_parts else ""
        raise GeminiImageGenerationError(f"Gemini returned no image data{detail}")
