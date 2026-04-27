from __future__ import annotations

import json
import logging
import re
import os
from pathlib import Path
from typing import Any

import requests

from src.kennybot.utils.app_settings import OLLAMA_MODEL_DEFAULT, OLLAMA_TIMEOUT_SEC
from src.kennybot.utils.prompts import get_prompt
from src.kennybot.utils.text import strip_ansi_and_ctrl

from src.kennybot.utils.profile_preview import (
    build_channel_profile_preview,
    build_profile_management_log,
    write_jsonl_log,
)


logger = logging.getLogger(__name__)


def _get_value(payload: dict[str, Any], name: str, default: Any, args: Any | None = None) -> Any:
    if name in payload and payload[name] is not None:
        return payload[name]
    if args is not None and hasattr(args, name):
        value = getattr(args, name)
        if value is not None:
            return value
    return default


def build_profile_preview_response(
    *,
    root: Path,
    payload: dict[str, Any] | None = None,
    args: Any | None = None,
    ai_client: Any | None = None,
    use_ai: bool | None = None,
) -> dict[str, Any]:
    data = payload or {}
    want_ai = _should_use_ai(data, args=args, use_ai=use_ai)
    model_name = _resolve_model_name(data, args=args)
    logger.info(
        "build profile preview: guild_id=%s channel_id=%s scope=%s question=%s ai_mode=%s ai_available=%s",
        _get_value(data, "guild_id", 972052382315855912, args=args),
        _get_value(data, "channel_id", 972052382315855912, args=args),
        _get_value(data, "scope", "auto", args=args),
        _get_value(data, "question", "このサーバーはなにするところ？", args=args),
        "ai" if want_ai else "fallback",
        bool(want_ai),
    )
    preview = build_channel_profile_preview(
        root=Path(_get_value(data, "root", root, args=args)),
        guild_id=_get_value(data, "guild_id", 972052382315855912, args=args),
        channel_id=_get_value(data, "channel_id", 972052382315855912, args=args),
        scope=str(_get_value(data, "scope", "auto", args=args)),
        question=str(_get_value(data, "question", "このサーバーはなにするところ？", args=args)),
        limit=int(_get_value(data, "limit", 6, args=args)),
        max_chars=int(_get_value(data, "max_chars", 2600, args=args)),
    )
    answer = preview["answer"]
    ai_status = _build_ai_status(want_ai=want_ai, model_name=model_name)
    if want_ai:
        answer, ai_status = _build_ai_profile_answer(
            preview,
            ai_client=ai_client,
            payload=data,
            args=args,
        )
    response = dict(preview)
    response["answer"] = answer
    response["ai_status"] = ai_status
    management_log = build_profile_management_log(response)
    response["management_log"] = management_log
    emit_log = bool(_get_value(data, "emit_log", False, args=args))
    if emit_log:
        log_file = Path(
            _get_value(
                data,
                "log_file",
                root / "runtime" / "logs" / "profile_preview.log",
                args=args,
            )
        )
        write_jsonl_log(log_file, management_log)
        logger.info("wrote management log to %s", log_file)
    return response


def _should_use_ai(payload: dict[str, Any], *, args: Any | None = None, use_ai: bool | None = None) -> bool:
    if use_ai is not None:
        return bool(use_ai)
    if "use_ai" in payload and payload["use_ai"] is not None:
        return bool(payload["use_ai"])
    if args is not None and hasattr(args, "no_ai") and bool(getattr(args, "no_ai")):
        return False
    if args is not None and hasattr(args, "use_ai") and getattr(args, "use_ai") is not None:
        return bool(getattr(args, "use_ai"))
    return True


def _build_ai_profile_answer(
    preview: dict[str, Any],
    *,
    ai_client: Any | None = None,
    payload: dict[str, Any] | None = None,
    args: Any | None = None,
) -> tuple[str, dict[str, Any]]:
    profile_block = _profile_block_for_ai(str(preview.get("profile") or ""))
    prompt = get_prompt("chat", "channel_profile_prompt").format(
        query=str(preview.get("question") or "このサーバーはなにするところ？"),
        channel_profile_block=profile_block,
    )
    model_name = _resolve_model_name(payload or {}, args=args)
    fallback_answer = str(preview.get("answer") or "")
    if ai_client is not None:
        try:
            answer = ai_client.chat_simple(
                model=str(model_name),
                prompt=prompt,
                stream=False,
                timeout_sec=OLLAMA_TIMEOUT_SEC,
            )
            normalized = _normalize_ai_answer(str(answer or ""), str(preview.get("question") or ""))
            if normalized:
                return normalized, {
                    "requested": True,
                    "available": True,
                    "mode": "ai",
                    "reason": "injected_client",
                    "model": str(model_name),
                }
            return fallback_answer, {
                "requested": True,
                "available": True,
                "mode": "fallback",
                "reason": "empty_ai_response",
                "model": str(model_name),
            }
        except Exception:
            logger.info("Preview AI generation failed; using fallback profile summary", exc_info=True)
            return fallback_answer, {
                "requested": True,
                "available": False,
                "mode": "fallback",
                "reason": "injected_client_failed",
                "model": str(model_name),
            }

    try:
        answer = _ollama_http_chat(prompt=prompt, model=str(model_name), payload=payload, args=args)
    except requests.HTTPError as exc:
        logger.info("Preview AI generation failed; using fallback profile summary", exc_info=True)
        reason = "ollama_model_missing" if getattr(exc.response, "status_code", None) == 404 else "ollama_http_error"
        return fallback_answer, {
            "requested": True,
            "available": False,
            "mode": "fallback",
            "reason": reason,
            "model": str(model_name),
        }
    except Exception:
        logger.info("Preview AI generation failed; using fallback profile summary", exc_info=True)
        return fallback_answer, {
            "requested": True,
            "available": False,
            "mode": "fallback",
            "reason": _ollama_failure_reason(),
            "model": str(model_name),
        }
    normalized = _normalize_ai_answer(str(answer or ""), str(preview.get("question") or ""))
    if normalized:
        return normalized, {
            "requested": True,
            "available": True,
            "mode": "ai",
            "reason": "ollama_http_ok",
            "model": str(model_name),
        }
    return fallback_answer, {
        "requested": True,
        "available": True,
        "mode": "fallback",
        "reason": "empty_ai_response",
        "model": str(model_name),
    }


def _build_ai_status(
    *,
    want_ai: bool,
    model_name: str = "",
) -> dict[str, Any]:
    if not want_ai:
        return {
            "requested": False,
            "available": False,
            "mode": "fallback",
            "reason": "disabled_by_request",
            "model": model_name,
        }
    return {
        "requested": True,
        "available": True,
        "mode": "ai",
        "reason": "ollama_http_requested",
        "model": model_name,
    }


def _resolve_model_name(payload: dict[str, Any], *, args: Any | None = None) -> str:
    model_name = _get_value(payload, "model", None, args=args) or _get_value(payload, "ollama_model", None, args=args)
    if not model_name and args is not None and hasattr(args, "ollama_model") and getattr(args, "ollama_model"):
        model_name = getattr(args, "ollama_model")
    if not model_name:
        model_name = OLLAMA_MODEL_DEFAULT or "gpt-oss:120b"
    return str(model_name)


def _profile_block_for_ai(profile_block: str) -> str:
    lines: list[str] = []
    for raw_line in str(profile_block or "").splitlines():
        line = strip_ansi_and_ctrl(raw_line).strip()
        if not line:
            continue
        if line == "---":
            continue
        if line.startswith("[RAG:") and "/" in line:
            continue
        if "chat_rag.md" in line and line.startswith("[") and line.endswith("]"):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _resolve_ollama_host(payload: dict[str, Any] | None = None, *, args: Any | None = None) -> str:
    host = _get_value(payload or {}, "ollama_host", None, args=args)
    if not host:
        host = os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434"
    host = str(host).strip()
    if not host:
        host = "http://127.0.0.1:11434"
    if "ollama.com" in host.lower():
        host = "http://127.0.0.1:11434"
    if "://" not in host:
        host = f"http://{host}"
    return host.rstrip("/")


def _ollama_http_chat(*, prompt: str, model: str, payload: dict[str, Any] | None = None, args: Any | None = None) -> str:
    host = _resolve_ollama_host(payload, args=args)
    url = f"{host}/api/chat"
    response = requests.post(
        url,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        },
        timeout=OLLAMA_TIMEOUT_SEC,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("unexpected ollama response")
    message = data.get("message") or {}
    if not isinstance(message, dict):
        raise ValueError("unexpected ollama response message")
    return str(message.get("content") or "")


def _ollama_failure_reason() -> str:
    return "ollama_http_unavailable"


def _normalize_ai_answer(answer: str, question: str) -> str:
    text = strip_ansi_and_ctrl(answer or "").strip()
    if not text:
        return ""
    text = re.sub(r"（モック応答）", "", text)
    text = re.sub(r"モック応答[:：\s]*", "", text)
    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue
        line = re.sub(r"^\[RAG:[^\]]+\]\s*", "", line)
        line = re.sub(r"\[RAG:[^\]]+\]", "", line)
        line = re.sub(r"^.*?を優先して返しました[。．.]*\s*", "", line)
        line = re.sub(r"^.*?を優先しました[。．.]*\s*", "", line)
        line = re.sub(r"^.*?を案内しました[。．.]*\s*", "", line)
        line = re.sub(r"^.*?を要約しました[。．.]*\s*", "", line)
        line = re.sub(r"^.*?を見て判断しました[。．.]*\s*", "", line)
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    text = text.replace("出典", "出展")
    text = re.sub(r"^(?:RAG(?:の)?(?:[A-Za-z0-9_.\-\/]+)?(?:です|である|である。|です。)?[、,：:\s]*)", "", text)
    text = re.sub(r"^(?:ここは、)?RAG(?:の)?(?:[A-Za-z0-9_.\-\/]+)?(?:です|である|である。|です。)?[、,：:\s]*", "", text)
    if "に対しては" in text[:80]:
        text = re.sub(r"^.*?に対しては[、,：:\s]*", "", text)
    if question and text.startswith(question):
        text = text[len(question):].lstrip("。．.、,:： \n\t")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) > 3:
        paragraphs = paragraphs[:3]
    text = "\n\n".join(paragraphs) if paragraphs else text
    return text


def parse_json_payload(text: str) -> dict[str, Any]:
    if not text:
        return {}
    loaded = json.loads(text)
    return loaded if isinstance(loaded, dict) else {}
