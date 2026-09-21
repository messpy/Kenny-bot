from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any

import requests

from src.kennybot.ai.decision.models import DecisionContext, ShadowDecision


ROUTES = {
    "normal_chat",
    "current_external_info",
    "server_internal_info",
    "person_lookup",
    "bot_capability",
    "local_activity",
    "repair_candidate",
    "other",
}


class JevProviderError(RuntimeError):
    def __init__(self, status: str, message: str = "") -> None:
        super().__init__(message or status)
        self.status = status


class JevProvider:
    """Small HTTP adapter. Jev-specific request/response details stay here."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.typesafe.ai",
        model: str = "jev-latest",
        timeout_seconds: float = 1.5,
        max_text_chars: int = 1200,
        schema_version: str = "jev-shadow-v1",
    ) -> None:
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._model = model.strip() or "jev-latest"
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self._max_text_chars = max(1, int(max_text_chars))
        self._schema_version = schema_version

    def _payload(self, context: DecisionContext) -> dict[str, object]:
        return {
            "model": self._model,
            "state": {
                "text": context.text[: self._max_text_chars],
                "has_reply": bool(context.has_reply),
                "has_mentions": bool(context.has_mentions),
                "is_dm": bool(context.is_dm),
                "attachment_types": list(context.attachment_types),
            },
            "questions": {
                "route": {
                    "type": "choice",
                    "instructions": "Classify the message into exactly one route.",
                    "criteria": {route: route for route in sorted(ROUTES)},
                },
                "needs_external_information": self._noul_question(
                    "Return the probability that current external information is required."
                ),
                "needs_rag": self._noul_question(
                    "Return the probability that internal RAG knowledge is required."
                ),
                "needs_server_info": self._noul_question(
                    "Return the probability that server or channel information is required."
                ),
                "repair_candidate": self._noul_question(
                    "Return the probability that this is a repair or bug report."
                ),
            },
        }

    @staticmethod
    def _noul_question(instructions: str) -> dict[str, object]:
        return {
            "type": "noul",
            "instructions": instructions,
            "criteria": {"true": "The condition applies.", "false": "The condition does not apply."},
        }

    def _request_sync(self, payload: dict[str, object]) -> dict[str, Any]:
        if not self._api_key:
            raise JevProviderError("connection_error", "Jev API key is not configured")
        response = requests.post(
            f"{self._base_url}/v1/systemone",
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=(self._timeout_seconds, self._timeout_seconds),
        )
        if response.status_code >= 400:
            raise JevProviderError("http_error", f"Jev HTTP status {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise JevProviderError("invalid_response", "Jev response was not JSON") from exc
        if not isinstance(body, dict):
            raise JevProviderError("schema_error", "Jev response must be an object")
        return body

    async def evaluate(self, context: DecisionContext) -> ShadowDecision:
        started = time.perf_counter()
        try:
            body = await asyncio.to_thread(self._request_sync, self._payload(context))
            decision = self._normalize(body)
            return ShadowDecision(
                **decision,
                latency_ms=(time.perf_counter() - started) * 1000,
                status="success",
            )
        except JevProviderError:
            raise
        except requests.Timeout as exc:
            raise JevProviderError("timeout", "Jev request timed out") from exc
        except requests.RequestException as exc:
            raise JevProviderError("connection_error", "Jev request failed") from exc
        except Exception as exc:
            raise JevProviderError("internal_error", "Jev response normalization failed") from exc

    def _normalize(self, body: dict[str, Any]) -> dict[str, object]:
        answers = body.get("answers")
        if not isinstance(answers, dict):
            raise JevProviderError("schema_error", "Jev response has no answers object")
        route_answer = answers.get("route")
        route = self._choice_value(route_answer)
        if route not in ROUTES:
            raise JevProviderError("schema_error", "Jev route is not in the configured criteria")
        route_probability = self._number(route_answer, ("probability", "confidence"), required=False)
        route_probabilities = self._probabilities(route_answer)
        return {
            "route": route,
            "route_probability": route_probability,
            "route_probabilities": route_probabilities,
            "external_information_probability": self._noul_probability(answers, "needs_external_information"),
            "rag_probability": self._noul_probability(answers, "needs_rag"),
            "server_info_probability": self._noul_probability(answers, "needs_server_info"),
            "repair_probability": self._noul_probability(answers, "repair_candidate"),
            "provider_version": str(body.get("model") or self._model),
        }

    @staticmethod
    def _choice_value(answer: object) -> str | None:
        if not isinstance(answer, dict):
            raise JevProviderError("schema_error", "Choice answer must be an object")
        value = answer.get("choice", answer.get("winner", answer.get("value")))
        return str(value).strip() if value is not None else None

    @classmethod
    def _number(cls, answer: object, keys: tuple[str, ...], *, required: bool) -> float | None:
        if not isinstance(answer, dict):
            raise JevProviderError("schema_error", "Answer must be an object")
        value = next((answer.get(key) for key in keys if key in answer), None)
        if value is None:
            if required:
                raise JevProviderError("schema_error", "Required probability is missing")
            return None
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise JevProviderError("schema_error", "Probability is not numeric") from exc
        if not math.isfinite(result) or not 0 <= result <= 1:
            raise JevProviderError("schema_error", "Probability is out of range")
        return result

    @classmethod
    def _probabilities(cls, answer: object) -> dict[str, float]:
        if not isinstance(answer, dict):
            raise JevProviderError("schema_error", "Choice answer must be an object")
        raw = answer.get("probabilities", answer.get("distribution", {}))
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise JevProviderError("schema_error", "Choice probabilities must be an object")
        result = {str(key): cls._number({"value": value}, ("value",), required=True) for key, value in raw.items()}
        if any(key not in ROUTES for key in result):
            raise JevProviderError("schema_error", "Unknown route probability key")
        return result

    @classmethod
    def _noul_probability(cls, answers: dict[str, Any], key: str) -> float:
        answer = answers.get(key)
        if isinstance(answer, dict):
            value = answer.get("probability", answer.get("value"))
        else:
            value = answer
        result = cls._number({"value": value}, ("value",), required=True)
        assert result is not None
        return result


def build_jev_provider(
    *,
    timeout_seconds: float = 1.5,
    max_text_chars: int = 1200,
    schema_version: str = "jev-shadow-v1",
) -> JevProvider | None:
    key = os.getenv("JEV_API_KEY", "").strip()
    if not key:
        return None
    return JevProvider(
        api_key=key,
        base_url=os.getenv("JEV_API_BASE_URL", "https://api.typesafe.ai"),
        model=os.getenv("JEV_MODEL", "jev-latest"),
        timeout_seconds=timeout_seconds,
        max_text_chars=max_text_chars,
        schema_version=schema_version,
    )
