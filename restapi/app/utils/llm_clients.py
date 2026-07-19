"""Common LLM providers for ontology generation.

The interface is based on official main's small client abstraction, extended
with native Ollama telemetry, deterministic options, preflight and typed
errors.  No client performs model pulls.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests


ERROR_CATEGORIES = {
    "connection_error",
    "timeout",
    "model_not_found",
    "invalid_request",
    "rate_limit",
    "server_error",
    "malformed_response",
    "empty_response",
    "unknown_error",
}
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
BACKOFF_SECONDS = (2.0, 4.0, 8.0)
MAX_ATTEMPTS = 3
OLLAMA_TELEMETRY_FIELDS = (
    "model",
    "created_at",
    "done",
    "done_reason",
    "total_duration",
    "load_duration",
    "prompt_eval_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
)


class ProviderError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        status_code: Optional[int] = None,
        attempts: int = 1,
        transient: bool = False,
        response_body: Optional[str] = None,
    ) -> None:
        if category not in ERROR_CATEGORIES:
            category = "unknown_error"
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.attempts = attempts
        self.transient = transient
        self.response_body = response_body

    def as_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "message": str(self),
            "status_code": self.status_code,
            "attempts": self.attempts,
            "transient": self.transient,
            "response_body": self.response_body,
        }


@dataclass(frozen=True)
class GenerationOptions:
    model: str
    temperature: float = 0.0
    seed: Optional[int] = 42
    num_ctx: Optional[int] = 8192
    max_output_tokens: int = 4096
    timeout_seconds: float = 1800.0
    keep_alive: str = "30m"
    stream: bool = False


@dataclass
class LLMResponse:
    content: str
    raw_response: Dict[str, Any]
    telemetry: Dict[str, Any] = field(default_factory=dict)
    provider: str = "unknown"
    attempts: int = 1


class BaseLLMClient:
    provider_name = "base"

    def chat_completion(
        self, messages: List[Dict[str, str]], options: GenerationOptions
    ) -> LLMResponse:
        raise NotImplementedError


def _error_from_status(status_code: int, body: str, attempts: int) -> ProviderError:
    lowered = body.lower()
    if status_code == 404 and "model" in lowered:
        category = "model_not_found"
    elif status_code == 429:
        category = "rate_limit"
    elif status_code >= 500:
        category = "server_error"
    elif status_code in {400, 404, 405, 409, 422}:
        category = "invalid_request"
    else:
        category = "unknown_error"
    return ProviderError(
        category,
        f"Ollama HTTP {status_code}: {body or 'empty response body'}",
        status_code=status_code,
        attempts=attempts,
        transient=status_code in TRANSIENT_STATUS_CODES,
        response_body=body,
    )


class OllamaAdapter(BaseLLMClient):
    provider_name = "ollama"

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        session: Optional[requests.Session] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        ).rstrip("/")
        self.session = session or requests.Session()
        self.sleeper = sleeper

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        timeout_seconds: float = 30.0,
        retry: bool = True,
    ) -> tuple[Dict[str, Any], int]:
        maximum = MAX_ATTEMPTS if retry else 1
        for attempt in range(1, maximum + 1):
            try:
                response = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    json=payload,
                    timeout=timeout_seconds,
                )
            except requests.Timeout as exc:
                error = ProviderError(
                    "timeout",
                    f"Ollama request timed out after {timeout_seconds} seconds",
                    attempts=attempt,
                    transient=True,
                )
            except requests.ConnectionError as exc:
                error = ProviderError(
                    "connection_error",
                    f"Could not connect to Ollama: {exc}",
                    attempts=attempt,
                    transient=True,
                )
            except requests.RequestException as exc:
                raise ProviderError(
                    "unknown_error", f"Ollama request failed: {exc}", attempts=attempt
                ) from exc
            else:
                if response.status_code >= 400:
                    error = _error_from_status(
                        response.status_code, response.text, attempt
                    )
                else:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise ProviderError(
                            "malformed_response",
                            "Ollama returned malformed JSON",
                            status_code=response.status_code,
                            attempts=attempt,
                            response_body=response.text,
                        ) from exc
                    if not isinstance(data, dict):
                        raise ProviderError(
                            "malformed_response",
                            "Ollama JSON response must be an object",
                            status_code=response.status_code,
                            attempts=attempt,
                        )
                    return data, attempt

            if not error.transient or attempt >= maximum:
                raise error
            self.sleeper(BACKOFF_SECONDS[attempt - 1])
        raise ProviderError("unknown_error", "Unreachable retry state")

    def chat_completion(
        self, messages: List[Dict[str, str]], options: GenerationOptions
    ) -> LLMResponse:
        if options.stream:
            raise ProviderError(
                "invalid_request", "Phase 4 native Ollama requires stream=false"
            )
        if not options.model.strip() or not messages:
            raise ProviderError(
                "invalid_request", "Ollama model and messages are required"
            )
        body: Dict[str, Any] = {
            "model": options.model,
            "messages": messages,
            "stream": False,
            "keep_alive": options.keep_alive,
            "options": {
                "temperature": options.temperature,
                "num_predict": options.max_output_tokens,
            },
        }
        if options.seed is not None:
            body["options"]["seed"] = options.seed
        if options.num_ctx is not None:
            body["options"]["num_ctx"] = options.num_ctx

        data, attempts = self._request_json(
            "POST",
            "/api/chat",
            payload=body,
            timeout_seconds=options.timeout_seconds,
            retry=True,
        )
        message = data.get("message")
        if not isinstance(message, dict) or "content" not in message:
            raise ProviderError(
                "malformed_response",
                "Ollama response is missing message.content",
                attempts=attempts,
            )
        content = message.get("content")
        if not isinstance(content, str):
            raise ProviderError(
                "malformed_response",
                "Ollama message.content must be a string",
                attempts=attempts,
            )
        if not content.strip():
            raise ProviderError(
                "empty_response", "Ollama generated empty content", attempts=attempts
            )
        telemetry = {
            key: data[key] for key in OLLAMA_TELEMETRY_FIELDS if key in data
        }
        return LLMResponse(
            content=content,
            raw_response=data,
            telemetry=telemetry,
            provider=self.provider_name,
            attempts=attempts,
        )

    def version(self, timeout_seconds: float = 30.0) -> Optional[str]:
        data, _ = self._request_json(
            "GET", "/api/version", timeout_seconds=timeout_seconds, retry=False
        )
        value = data.get("version")
        return str(value) if value is not None else None

    def health(self, timeout_seconds: float = 30.0) -> Dict[str, Any]:
        return {"healthy": True, "version": self.version(timeout_seconds)}

    def list_models(self, timeout_seconds: float = 30.0) -> List[Dict[str, Any]]:
        data, _ = self._request_json(
            "GET", "/api/tags", timeout_seconds=timeout_seconds, retry=False
        )
        models = data.get("models", [])
        if not isinstance(models, list):
            raise ProviderError(
                "malformed_response", "Ollama /api/tags models must be a list"
            )
        return [model for model in models if isinstance(model, dict)]

    def preflight(
        self, requested_model: str, timeout_seconds: float = 30.0
    ) -> Dict[str, Any]:
        version = self.version(timeout_seconds)
        models = self.list_models(timeout_seconds)
        match = next(
            (
                model
                for model in models
                if str(model.get("name") or model.get("model") or "")
                == requested_model
            ),
            None,
        )
        return {
            "healthy": True,
            "version": version,
            "requested_model": requested_model,
            "model_available": match is not None,
            "model_digest": match.get("digest") if match else None,
            "installed_models": [
                str(model.get("name") or model.get("model") or "") for model in models
            ],
            "missing_model_error": (
                None
                if match
                else {
                    "category": "model_not_found",
                    "message": f"Ollama model is not installed: {requested_model}",
                }
            ),
        }


class OpenAIAdapter(BaseLLMClient):
    provider_name = "openai"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        try:
            from openai import OpenAI
        except Exception as exc:
            raise RuntimeError("OpenAI SDK not available (install `openai`).") from exc
        kwargs: Dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs) if kwargs else OpenAI()

    def chat_completion(
        self, messages: List[Dict[str, str]], options: GenerationOptions
    ) -> LLMResponse:
        if options.model.lower().startswith("gpt-5"):
            prompt = "\n\n".join(message.get("content", "") for message in messages)
            response = self.client.responses.create(
                model=options.model,
                input=prompt,
                max_output_tokens=options.max_output_tokens,
            )
            content = str(getattr(response, "output_text", "") or "")
        else:
            response = self.client.chat.completions.create(
                model=options.model,
                messages=messages,
                max_tokens=options.max_output_tokens,
                temperature=options.temperature,
                seed=options.seed,
            )
            content = str(response.choices[0].message.content or "")
        if not content.strip():
            raise ProviderError("empty_response", "OpenAI generated empty content")
        if hasattr(response, "model_dump"):
            raw = response.model_dump(mode="json")
        else:
            raw = {"text": str(response)}
        return LLMResponse(
            content=content,
            raw_response=raw,
            telemetry={"model": raw.get("model")},
            provider=self.provider_name,
        )


def get_llm_client(provider: Optional[str] = None, **kwargs: Any) -> BaseLLMClient:
    name = (provider or os.getenv("LLM_PROVIDER", "openai")).strip().lower()
    if name in {"openai", "openai.com"}:
        return OpenAIAdapter(
            api_key=kwargs.get("api_key") or os.getenv("OPENAI_API_KEY"),
            base_url=kwargs.get("base_url"),
        )
    if name == "ollama":
        return OllamaAdapter(
            base_url=kwargs.get("base_url") or os.getenv("OLLAMA_BASE_URL"),
            session=kwargs.get("session"),
            sleeper=kwargs.get("sleeper", time.sleep),
        )
    raise ProviderError("invalid_request", f"Unsupported LLM provider: {name}")
