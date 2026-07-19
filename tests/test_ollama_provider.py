from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from restapi.app.utils.llm_clients import (
    GenerationOptions,
    OllamaAdapter,
    ProviderError,
)


@contextmanager
def mock_ollama(responses):
    state = {"responses": list(responses), "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _serve(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw) if raw else None
            state["requests"].append(
                {"method": self.command, "path": self.path, "json": payload}
            )
            response = state["responses"].pop(0)
            if response.get("delay"):
                time.sleep(response["delay"])
            body = response.get("body", {})
            encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(response.get("status", 200))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = _serve
        do_POST = _serve

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def options(**updates):
    values = {
        "model": "phase4-model",
        "temperature": 0,
        "seed": 42,
        "num_ctx": 8192,
        "max_output_tokens": 4096,
        "timeout_seconds": 1,
        "keep_alive": "30m",
        "stream": False,
    }
    values.update(updates)
    return GenerationOptions(**values)


def valid_response():
    return {
        "model": "phase4-model",
        "created_at": "2026-07-15T00:00:00Z",
        "message": {"role": "assistant", "content": "valid turtle"},
        "done": True,
        "done_reason": "stop",
        "total_duration": 100,
        "load_duration": 10,
        "prompt_eval_count": 20,
        "prompt_eval_duration": 30,
        "eval_count": 4,
        "eval_duration": 40,
    }


def test_native_chat_schema_options_and_telemetry():
    with mock_ollama([{"body": valid_response()}]) as (url, state):
        result = OllamaAdapter(url).chat_completion(
            [{"role": "user", "content": "prompt"}], options()
        )
    request = state["requests"][0]
    assert request["path"] == "/api/chat"
    assert request["json"] == {
        "model": "phase4-model",
        "messages": [{"role": "user", "content": "prompt"}],
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "temperature": 0,
            "seed": 42,
            "num_ctx": 8192,
            "num_predict": 4096,
        },
    }
    assert result.content == "valid turtle"
    assert result.telemetry["done_reason"] == "stop"
    assert result.telemetry["prompt_eval_count"] == 20
    assert result.telemetry["eval_duration"] == 40


def test_transient_500_retries_with_backoff():
    delays = []
    with mock_ollama(
        [{"status": 500, "body": {"error": "busy"}}, {"body": valid_response()}]
    ) as (url, state):
        result = OllamaAdapter(url, sleeper=delays.append).chat_completion(
            [{"role": "user", "content": "prompt"}], options()
        )
    assert result.attempts == 2
    assert len(state["requests"]) == 2
    assert delays == [2.0]


def test_permanent_404_model_not_found_is_not_retried():
    with mock_ollama(
        [{"status": 404, "body": {"error": "model not found"}}]
    ) as (url, state):
        with pytest.raises(ProviderError) as caught:
            OllamaAdapter(url, sleeper=lambda _: None).chat_completion(
                [{"role": "user", "content": "prompt"}], options()
            )
    assert caught.value.category == "model_not_found"
    assert caught.value.attempts == 1
    assert len(state["requests"]) == 1


def test_timeout_is_normalized_and_retried_three_times():
    replies = [{"delay": 0.05, "body": valid_response()} for _ in range(3)]
    with mock_ollama(replies) as (url, state):
        with pytest.raises(ProviderError) as caught:
            OllamaAdapter(url, sleeper=lambda _: None).chat_completion(
                [{"role": "user", "content": "prompt"}],
                options(timeout_seconds=0.005),
            )
    assert caught.value.category == "timeout"
    assert caught.value.attempts == 3
    assert len(state["requests"]) == 3


@pytest.mark.parametrize(
    "status,body,category,attempts",
    [
        (400, {"error": "bad request"}, "invalid_request", 1),
        (429, {"error": "rate limited"}, "rate_limit", 3),
        (503, {"error": "unavailable"}, "server_error", 3),
    ],
)
def test_http_error_categories_and_retry_limits(status, body, category, attempts):
    with mock_ollama(
        [{"status": status, "body": body} for _ in range(attempts)]
    ) as (url, state):
        with pytest.raises(ProviderError) as caught:
            OllamaAdapter(url, sleeper=lambda _: None).chat_completion(
                [{"role": "user", "content": "prompt"}], options()
            )
    assert caught.value.category == category
    assert caught.value.attempts == attempts
    assert len(state["requests"]) == attempts


def test_connection_error_is_normalized_and_retried():
    class DisconnectedSession:
        def request(self, *_args, **_kwargs):
            raise requests.ConnectionError("mock connection refused")

    with pytest.raises(ProviderError) as caught:
        OllamaAdapter(
            "http://mock.invalid",
            session=DisconnectedSession(),
            sleeper=lambda _: None,
        ).chat_completion([{"role": "user", "content": "prompt"}], options())
    assert caught.value.category == "connection_error"
    assert caught.value.attempts == 3


@pytest.mark.parametrize(
    "body,category",
    [
        (b"not-json", "malformed_response"),
        ({"done": True}, "malformed_response"),
        ({"message": {"content": ""}}, "empty_response"),
    ],
)
def test_malformed_and_empty_responses(body, category):
    with mock_ollama([{"body": body}]) as (url, _state):
        with pytest.raises(ProviderError) as caught:
            OllamaAdapter(url).chat_completion(
                [{"role": "user", "content": "prompt"}], options()
            )
    assert caught.value.category == category


def test_health_version_model_listing_and_digest_preflight():
    responses = [
        {"body": {"version": "0.12.0"}},
        {
            "body": {
                "models": [
                    {"name": "phase4-model", "digest": "sha256:abc"},
                    {"name": "other", "digest": "sha256:def"},
                ]
            }
        },
    ]
    with mock_ollama(responses) as (url, state):
        result = OllamaAdapter(url).preflight("phase4-model")
    assert [request["path"] for request in state["requests"]] == [
        "/api/version",
        "/api/tags",
    ]
    assert result["healthy"] is True
    assert result["version"] == "0.12.0"
    assert result["model_available"] is True
    assert result["model_digest"] == "sha256:abc"
    assert result["missing_model_error"] is None


def test_explicit_health_function_records_version():
    with mock_ollama([{"body": {"version": "0.12.0"}}]) as (url, state):
        result = OllamaAdapter(url).health()
    assert result == {"healthy": True, "version": "0.12.0"}
    assert state["requests"][0]["path"] == "/api/version"


def test_missing_model_preflight_is_clear_and_never_pulls():
    with mock_ollama(
        [{"body": {"version": "0.12.0"}}, {"body": {"models": []}}]
    ) as (url, state):
        result = OllamaAdapter(url).preflight("missing")
    assert result["model_available"] is False
    assert result["missing_model_error"]["category"] == "model_not_found"
    assert all(request["path"] != "/api/pull" for request in state["requests"])
