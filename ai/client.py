# ai/client.py
# Ollama Client 統合（ollama_util.py の utilities を使用）

import logging
import os
import inspect
from threading import Lock
from typing import Any, Iterator, Optional, get_args, get_origin
from urllib.parse import urlparse

import requests

try:
    from ollama import Client, ResponseError
except ImportError:
    raise ImportError("ollama が必要です。pip install ollama を実行してください。")

logger = logging.getLogger(__name__)
_GEMINI_API_BASE = os.getenv(
    "GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta"
).rstrip("/")
_OLLAMA_FALLBACK_MODEL = (
    os.getenv("OLLAMA_FALLBACK_MODEL", "gpt-oss:120b").strip() or "gpt-oss:120b"
)
_KNOWN_GEMINI_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-embedding-001",
)

try:
    from webduck import ollama as webduck_ollama
except ImportError:
    webduck_ollama = None


class OllamaClientConfig:
    """Ollama Client 設定"""
    
    def __init__(
        self,
        host: Optional[str] = None,
        api_key: Optional[str] = None,
        api_key_env: str = "OLLAMA_API_KEY",
    ):
        self.host = host
        self.api_key = api_key or os.getenv(api_key_env)

    def _is_local_host(self) -> bool:
        if not self.host:
            return True
        parsed = urlparse(self.host if "://" in self.host else f"http://{self.host}")
        hostname = (parsed.hostname or "").lower()
        return hostname in {"localhost", "127.0.0.1", "::1", "ollama"}
    
    def build_client(self) -> Client:
        """Client インスタンスを構築"""
        if self.host:
            if self._is_local_host():
                old_api_key = os.environ.pop("OLLAMA_API_KEY", None)
                try:
                    return Client(host=self.host)
                finally:
                    if old_api_key is not None:
                        os.environ["OLLAMA_API_KEY"] = old_api_key
            if not self.api_key:
                raise ValueError(
                    f"リモート ollama を使う場合は API キーが必要です "
                    f"(環境変数 OLLAMA_API_KEY またはコンストラクタ引数)"
                )
            return Client(
                host=self.host,
                headers={"Authorization": "Bearer " + self.api_key},
            )
        # ローカル ollama
        return Client()


class OllamaClientService:
    """Ollama Client ラッパー"""
    
    def __init__(self, config: OllamaClientConfig):
        self.config = config
        self.client = config.build_client()
        self._local_fallback_client = None
        if self.config.host and not self.config._is_local_host():
            try:
                self._local_fallback_client = OllamaClientConfig(host="http://127.0.0.1:11434").build_client()
            except Exception:
                logger.exception("Failed to initialize local Ollama fallback client")
        self._pull_lock = Lock()
        self._ensured_models: set[str] = set()
        self._embed_disabled = False
        self._http = requests.Session()

    def _gemini_api_key(self) -> str:
        return (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or ""
        ).strip()

    def _is_gemini_model(self, model: str) -> bool:
        value = (model or "").strip().lower()
        return value.startswith("gemini") or value.startswith("models/gemini")

    def _gemini_model_name(self, model: str) -> str:
        value = (model or "").strip()
        if value.startswith("models/"):
            return value
        return f"models/{value}"

    def _build_gemini_prompt_from_messages(self, messages: list[dict]) -> str:
        lines: list[str] = []
        for item in messages:
            role = str(item.get("role") or "user").strip().lower()
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if role == "system":
                lines.append(f"[System]\n{content}")
            elif role == "assistant":
                lines.append(f"[Assistant]\n{content}")
            elif role == "tool":
                tool_name = str(item.get("tool_name") or "tool")
                lines.append(f"[Tool:{tool_name}]\n{content}")
            else:
                lines.append(f"[User]\n{content}")
        return "\n\n".join(lines).strip()

    def _gemini_schema_type(self, annotation: object) -> str:
        origin = get_origin(annotation)
        if origin is not None:
            args = [arg for arg in get_args(annotation) if arg is not type(None)]
            if args:
                return self._gemini_schema_type(args[0])
        if annotation is int:
            return "INTEGER"
        if annotation is float:
            return "NUMBER"
        if annotation is bool:
            return "BOOLEAN"
        if annotation in {list, tuple}:
            return "ARRAY"
        return "STRING"

    def _gemini_function_declaration(self, tool: object) -> dict[str, Any]:
        name = str(getattr(tool, "__name__", "") or "").strip()
        if not name:
            raise ValueError("tool name is required")
        doc = inspect.getdoc(tool) or f"Call tool `{name}`."
        signature = inspect.signature(tool)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param_name, param in signature.parameters.items():
            if param.kind not in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }:
                continue
            schema: dict[str, Any] = {
                "type": self._gemini_schema_type(param.annotation),
                "description": f"Parameter `{param_name}` for tool `{name}`.",
            }
            properties[param_name] = schema
            if param.default is inspect._empty:
                required.append(param_name)
        declaration: dict[str, Any] = {
            "name": name,
            "description": doc[:1024],
            "parameters": {
                "type": "OBJECT",
                "properties": properties,
            },
        }
        if required:
            declaration["parameters"]["required"] = required
        return declaration

    def _gemini_tools_payload(self, tools: list[object]) -> list[dict[str, Any]]:
        declarations: list[dict[str, Any]] = []
        for tool in tools:
            try:
                declarations.append(self._gemini_function_declaration(tool))
            except Exception:
                logger.exception("Failed to convert tool for Gemini: %s", tool)
        return [{"functionDeclarations": declarations}] if declarations else []

    def _gemini_contents_from_messages(
        self, messages: list[dict]
    ) -> tuple[list[dict[str, Any]], str]:
        contents: list[dict[str, Any]] = []
        system_lines: list[str] = []
        for item in messages:
            role = str(item.get("role") or "user").strip().lower()
            if role == "system":
                content = str(item.get("content") or "").strip()
                if content:
                    system_lines.append(content)
                continue

            gemini_content = item.get("gemini_content")
            if isinstance(gemini_content, dict):
                contents.append(dict(gemini_content))
                continue

            content = str(item.get("content") or "").strip()
            if role == "assistant":
                if content:
                    contents.append({"role": "model", "parts": [{"text": content}]})
                continue
            if role == "tool":
                tool_name = str(item.get("tool_name") or "tool")
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": tool_name,
                                    "response": {"result": content},
                                }
                            }
                        ],
                    }
                )
                continue
            if content:
                contents.append({"role": "user", "parts": [{"text": content}]})
        return contents, "\n\n".join(system_lines).strip()

    def _extract_gemini_text(self, response: dict) -> str:
        candidates = response.get("candidates", [])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            texts: list[str] = []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text = str(part.get("text") or "").strip()
                if text:
                    texts.append(text)
            if texts:
                return "\n".join(texts).strip()
        return ""

    def _extract_gemini_tool_calls(self, response: dict) -> list[dict[str, Any]]:
        candidates = response.get("candidates", [])
        calls: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content") or {}
            parts = content.get("parts") or []
            for index, part in enumerate(parts):
                if not isinstance(part, dict):
                    continue
                function_call = part.get("functionCall") or {}
                name = str(function_call.get("name") or "").strip()
                if not name:
                    continue
                args = function_call.get("args") or {}
                call: dict[str, Any] = {
                    "id": f"gemini-call-{index}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": args if isinstance(args, dict) else {},
                    },
                }
                calls.append(call)
        return calls

    def _gemini_generation_config(self, format: Optional[str | dict] = None) -> dict:
        if format == "json":
            return {"responseMimeType": "application/json"}
        return {}

    def _gemini_generate_content(
        self,
        *,
        model: str,
        messages: list[dict],
        stream: bool = False,
        format: Optional[str | dict] = None,
        **kwargs,
    ):
        api_key = self._gemini_api_key()
        if not api_key:
            raise RuntimeError("Gemini を使うには GEMINI_API_KEY か GOOGLE_API_KEY が必要です。")

        tools = list(kwargs.pop("tools", []) or [])
        contents, system_instruction = self._gemini_contents_from_messages(messages)
        if not contents and not system_instruction:
            return {"message": {"role": "assistant", "content": ""}}

        payload: dict[str, Any] = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        generation_config = self._gemini_generation_config(format=format)
        if generation_config:
            payload["generationConfig"] = generation_config
        gemini_tools = self._gemini_tools_payload(tools)
        if gemini_tools:
            payload["tools"] = gemini_tools

        timeout = kwargs.pop("timeout", None) or 60
        url = f"{_GEMINI_API_BASE}/{self._gemini_model_name(model)}:generateContent"
        response = self._http.post(
            url,
            params={"key": api_key},
            json=payload,
            timeout=timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as err:
            status_code = getattr(err.response, "status_code", None)
            if status_code == 429:
                fallback_client = self._local_fallback_client or self.client
                fallback_model = _OLLAMA_FALLBACK_MODEL
                logger.warning(
                    "Gemini rate limited for model %s; falling back to Ollama model %s",
                    model,
                    fallback_model,
                )
                try:
                    return fallback_client.chat(
                        model=fallback_model,
                        messages=messages,
                        stream=stream,
                        format=format,
                        **kwargs,
                    )
                except Exception:
                    logger.exception("Ollama fallback failed after Gemini rate limit")
                    raise
            raise
        data = response.json()
        text = self._extract_gemini_text(data)
        candidates = data.get("candidates") or []
        first_content = {}
        if candidates and isinstance(candidates[0], dict):
            first_content = dict(candidates[0].get("content") or {})
        normalized = {
            "message": {
                "role": "assistant",
                "content": text,
                "tool_calls": self._extract_gemini_tool_calls(data),
                "gemini_content": first_content,
            }
        }
        if stream:
            def _single_chunk() -> Iterator[dict]:
                yield normalized
            return _single_chunk()
        return normalized

    def _is_model_missing_error(self, err: Exception) -> bool:
        if not isinstance(err, ResponseError):
            return False
        text = (getattr(err, "error", "") or str(err)).lower()
        return "not found" in text and "model" in text

    def _is_unauthorized_error(self, err: Exception) -> bool:
        if not isinstance(err, ResponseError):
            return False
        text = (getattr(err, "error", "") or str(err)).lower()
        return "unauthorized" in text or getattr(err, "status_code", None) == 401

    def _ensure_model_available(self, model: str) -> None:
        if model in self._ensured_models:
            return
        with self._pull_lock:
            if model in self._ensured_models:
                return
            logger.info("Model '%s' not found. Pulling via Ollama.", model)
            self.client.pull(model=model, stream=False)
            self._ensured_models.add(model)

    def _normalize_local_fallback_model(self, model: str) -> str:
        value = (model or "").strip()
        if value.endswith("-cloud"):
            return value[: -len("-cloud")]
        return value

    def _try_local_chat_fallback(
        self,
        *,
        model: str,
        messages: list[dict],
        stream: bool,
        format: Optional[str | dict],
        **kwargs,
    ):
        if self._local_fallback_client is None:
            raise RuntimeError("local fallback client is not available")
        local_model = self._normalize_local_fallback_model(model)
        logger.warning("Falling back to local Ollama model '%s' after remote failure", local_model)
        return self._local_fallback_client.chat(
            model=local_model,
            messages=messages,
            stream=stream,
            format=format,
            **kwargs,
        )

    def _chat_with_auto_pull(
        self,
        model: str,
        messages: list[dict],
        stream: bool = False,
        format: Optional[str | dict] = None,
        **kwargs,
    ):
        if self._is_gemini_model(model):
            return self._gemini_generate_content(
                model=model,
                messages=messages,
                stream=stream,
                format=format,
                **kwargs,
            )
        try:
            return self.client.chat(
                model=model,
                messages=messages,
                stream=stream,
                format=format,
                **kwargs,
            )
        except Exception as err:
            if self._is_model_missing_error(err):
                self._ensure_model_available(model)
                return self.client.chat(
                    model=model,
                    messages=messages,
                    stream=stream,
                    format=format,
                    **kwargs,
                )
            if self._local_fallback_client is not None:
                try:
                    return self._try_local_chat_fallback(
                        model=model,
                        messages=messages,
                        stream=stream,
                        format=format,
                        **kwargs,
                    )
                except Exception:
                    logger.exception("Local Ollama fallback failed")
            raise
    
    def chat(
        self,
        model: str,
        messages: list[dict],
        stream: bool = False,
        format: Optional[str | dict] = None,
        **kwargs,
    ):
        """
        チャット実行（streaming/non-streaming 対応）
        
        Args:
            model: モデル名
            messages: メッセージリスト [{"role": "user", "content": "..."}, ...]
            stream: ストリーミングするか
            format: JSON スキーマ（dict または "json" 文字列）
            **kwargs: その他オプション
        
        Returns:
            streaming 時は generator、非 streaming 時は response
        """
        return self._chat_with_auto_pull(
            model=model,
            messages=messages,
            stream=stream,
            format=format,
            **kwargs,
        )

    def has_web_tools(self) -> bool:
        # Gemini requests are routed directly via REST and do not expose local web tools here.
        if webduck_ollama is not None:
            return True
        has_methods = callable(getattr(self.client, "web_search", None)) and callable(getattr(self.client, "web_fetch", None))
        return has_methods and bool(self.config.api_key or os.getenv("OLLAMA_API_KEY"))

    def has_embed(self) -> bool:
        return (not self._embed_disabled) and callable(getattr(self.client, "embed", None))

    def pull_model(self, model: str) -> None:
        model = (model or "").strip()
        if not model:
            raise ValueError("model is required")
        self.client.pull(model=model, stream=False)

    def list_model_names(self) -> list[str]:
        names: list[str] = []
        try:
            response = self.client.list()
            models = response.get("models", []) if isinstance(response, dict) else []
            for item in models:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("model") or item.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
        except Exception:
            logger.exception("Failed to list Ollama models")
        if self._gemini_api_key():
            for model in _KNOWN_GEMINI_MODELS:
                if model not in names:
                    names.append(model)
        return names

    def _format_web_search_response(self, response: object) -> str:
        results = []
        if isinstance(response, dict):
            results = list(response.get("results") or [])
        else:
            results = list(getattr(response, "results", None) or [])

        lines: list[str] = []
        for idx, item in enumerate(results, start=1):
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                url = str(item.get("url") or "").strip()
                content = str(item.get("content") or "").strip()
            else:
                title = str(getattr(item, "title", "") or "").strip()
                url = str(getattr(item, "url", "") or "").strip()
                content = str(getattr(item, "content", "") or "").strip()
            if not (title or url or content):
                continue
            lines.append(f"{idx}. {title}\nURL: {url}\n概要: {content[:1200]}")
        return "\n\n".join(lines)

    def _format_web_fetch_response(self, response: object) -> str:
        if isinstance(response, dict):
            title = str(response.get("title") or "").strip()
            content = str(response.get("content") or "").strip()
            links = list(response.get("links") or [])
        else:
            title = str(getattr(response, "title", "") or "").strip()
            content = str(getattr(response, "content", "") or "").strip()
            links = list(getattr(response, "links", None) or [])

        lines = []
        if title:
            lines.append(f"Title: {title}")
        if content:
            lines.append(content[:6000])
        if links:
            lines.append("Links:\n" + "\n".join(str(link) for link in links[:20]))
        return "\n\n".join(lines).strip()

    def web_search(self, query: str, max_results: int = 3) -> str:
        """Search the web for up-to-date information."""
        if webduck_ollama is not None:
            try:
                response = webduck_ollama.web_search(query)
                return self._format_web_search_response(response) or str(response)
            except Exception as err:
                logger.exception("webduck web_search failed")
                logger.info("Falling back to Ollama client web_search")
        tool = getattr(self.client, "web_search", None)
        if not callable(tool):
            raise RuntimeError("web_search is not available in the current Ollama client")
        try:
            response = tool(query=query, max_results=max(1, min(int(max_results), 10)))
            return self._format_web_search_response(response)
        except Exception as err:
            logger.exception("web_search failed")
            return f"web_search failed: {err}"

    def web_fetch(self, url: str) -> str:
        """Fetch the contents of a web page by URL."""
        if webduck_ollama is not None and callable(getattr(webduck_ollama, "web_fetch", None)):
            try:
                response = webduck_ollama.web_fetch(url)
                return self._format_web_fetch_response(response) or str(response)
            except Exception as err:
                logger.exception("webduck web_fetch failed")
                return f"web_fetch failed: {err}"
        tool = getattr(self.client, "web_fetch", None)
        if not callable(tool):
            raise RuntimeError("web_fetch is not available in the current Ollama client")
        try:
            response = tool(url=url)
            return self._format_web_fetch_response(response)
        except Exception as err:
            logger.exception("web_fetch failed")
            return f"web_fetch failed: {err}"

    def embed(self, model: str, input_texts: str | list[str]) -> list[list[float]]:
        if self._embed_disabled:
            return []
        tool = getattr(self.client, "embed", None)
        if not callable(tool):
            raise RuntimeError("embed is not available in the current Ollama client")
        try:
            response = tool(model=model, input=input_texts)
        except Exception as err:
            if self._is_unauthorized_error(err):
                self._embed_disabled = True
                logger.warning("Disabling Ollama embed calls after unauthorized response")
                return []
            if not self._is_model_missing_error(err):
                logger.exception("embed failed")
                return []
            self._ensure_model_available(model)
            try:
                response = tool(model=model, input=input_texts)
            except Exception:
                logger.exception("embed retry failed")
                return []

        embeddings = []
        try:
            if isinstance(response, dict):
                embeddings = list(response.get("embeddings") or [])
            else:
                embeddings = list(getattr(response, "embeddings", None) or [])
            return [list(map(float, item)) for item in embeddings if isinstance(item, (list, tuple))]
        except Exception:
            logger.exception("Failed to parse embeddings response")
            return []

    def chat_simple(
        self,
        model: str,
        prompt: str,
        stream: bool = False,
        **kwargs,
    ) -> str | None:
        """
        シンプルなチャット（テキスト → テキスト）
        
        Args:
            model: モデル名
            prompt: プロンプト文字列
            stream: ストリーミング
            **kwargs: その他オプション
        
        Returns:
            応答テキスト（streaming 時は最後の chunk）
        """
        messages = [{"role": "user", "content": prompt}]
        
        if stream:
            last_content = None
            try:
                for chunk in self._chat_with_auto_pull(
                    model=model,
                    messages=messages,
                    stream=True,
                    **kwargs,
                ):
                    msg = chunk.get("message", {}) if isinstance(chunk, dict) else getattr(chunk, "message", {}) or {}
                    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
                    if content:
                        last_content = content
            except Exception:
                logger.exception("chat_simple stream failed")
                return None
            return last_content
        else:
            try:
                resp = self._chat_with_auto_pull(model=model, messages=messages, **kwargs)
                msg = resp.get("message", {}) if isinstance(resp, dict) else getattr(resp, "message", {}) or {}
                return msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
            except Exception:
                logger.exception("chat_simple failed")
                return None


def create_ollama_client(
    host: Optional[str] = None,
    api_key_env: str = "OLLAMA_API_KEY",
) -> OllamaClientService:
    """
    Ollama Client を作成（ローカルまたはリモート）
    
    Args:
        host: リモート ollama のホスト (e.g. https://ollama.com)
        api_key_env: API キーが格納された環境変数名
    
    Returns:
        OllamaClientService インスタンス
    """
    config = OllamaClientConfig(host=host, api_key_env=api_key_env)
    return OllamaClientService(config)
