"""Универсальный OpenAI-совместимый клиент: формат запроса, стратегии авторизации, разбор ответа."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from app.core.config import Settings
from app.domain.exceptions import LLMInvalidResponseError
from app.infrastructure.llm import http_retry_mixin
from app.infrastructure.llm.openai_compatible_client import (
    OpenAICompatibleClient,
    extract_json_object,
)

pytestmark = pytest.mark.asyncio

SUGGESTIONS = {
    "suggestions": [
        {
            "section_ref": "Сроки",
            "change_type": "add",
            "old_text": "Срок ответа — 10 дней.",
            "new_text": "Срок может быть продлён.",
            "rationale": "В источнике есть пункт о продлении.",
            "confidence_score": 0.8,
        }
    ]
}


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "llm_provider": "openai_compatible",
        "llm_endpoint": "http://llm.test/v1",
        "llm_model": "test-model",
        "llm_api_key": "secret-key",
        "llm_auth": "bearer",
        "llm_oauth_url": "http://oauth.test/api/v2/oauth",
        "llm_oauth_scope": "GIGACHAT_API_PERS",
        "llm_ca_bundle": "",
        "llm_verify_ssl": True,
        "llm_temperature": 0.1,
        "llm_timeout_seconds": 5,
        "llm_max_retries": 1,
        "llm_max_input_chars": 10_000,
    }
    base.update(overrides)
    return cast(Settings, SimpleNamespace(**base))


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
    )


def _install(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]):
    real_client = httpx.AsyncClient
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(
        http_retry_mixin.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(recording), **kwargs),
    )
    return calls


async def test_bearer_request_shape_and_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    fenced = (
        "Вот правки:\n```json\n" + json.dumps(SUGGESTIONS, ensure_ascii=False) + "\n```\nГотово."
    )
    calls = _install(monkeypatch, lambda r: chat_response(fenced))

    batch = await OpenAICompatibleClient(settings()).generate_suggestions("doc", "src", "md")

    assert len(calls) == 1
    request = calls[0]
    assert str(request.url) == "http://llm.test/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer secret-key"
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["messages"][0]["role"] == "user"
    assert "<document>\ndoc\n</document>" in body["messages"][0]["content"]
    assert body["temperature"] == 0.1

    assert len(batch.items) == 1
    assert batch.items[0].change_type == "add"
    assert batch.items[0].old_text == "Срок ответа — 10 дней."
    assert batch.raw_response == fenced


async def test_no_auth_sends_no_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, lambda r: chat_response(json.dumps(SUGGESTIONS)))
    await OpenAICompatibleClient(settings(llm_auth="none", llm_api_key="")).generate_suggestions(
        "doc", "src", "txt"
    )
    assert "authorization" not in calls[0].headers


def _gigachat_handler(tokens: list[str], reject_tokens: set[str] | None = None):
    issued = iter(tokens)
    reject_tokens = reject_tokens or set()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth.test":
            assert request.headers["Authorization"] == "Basic base64-auth-key"
            assert len(request.headers["RqUID"]) == 36
            assert request.content == b"scope=GIGACHAT_API_PERS"
            return httpx.Response(
                200,
                json={"access_token": next(issued), "expires_at": int((time.time() + 1800) * 1000)},
            )
        token = request.headers["Authorization"].removeprefix("Bearer ")
        if token in reject_tokens:
            return httpx.Response(401, json={"message": "Token has expired"})
        return chat_response(json.dumps(SUGGESTIONS))

    return handler


async def test_gigachat_token_is_fetched_once_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, _gigachat_handler(["t1"]))
    client = OpenAICompatibleClient(
        settings(llm_auth="oauth_gigachat", llm_api_key="base64-auth-key")
    )

    await client.generate_suggestions("doc", "src", "md")
    await client.generate_suggestions("doc", "src", "md")

    oauth_calls = [c for c in calls if c.url.host == "oauth.test"]
    chat_calls = [c for c in calls if c.url.host == "llm.test"]
    assert len(oauth_calls) == 1
    assert [c.headers["Authorization"] for c in chat_calls] == ["Bearer t1", "Bearer t1"]


async def test_gigachat_refreshes_token_on_401_and_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(monkeypatch, _gigachat_handler(["stale", "fresh"], reject_tokens={"stale"}))
    client = OpenAICompatibleClient(
        settings(llm_auth="oauth_gigachat", llm_api_key="base64-auth-key")
    )

    batch = await client.generate_suggestions("doc", "src", "md")

    assert len(batch.items) == 1
    chat_auth = [c.headers["Authorization"] for c in calls if c.url.host == "llm.test"]
    assert chat_auth == ["Bearer stale", "Bearer fresh"]
    assert sum(1 for c in calls if c.url.host == "oauth.test") == 2


async def test_bearer_401_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, lambda r: httpx.Response(401, text="bad key"))
    with pytest.raises(LLMInvalidResponseError, match="HTTP 401") as exc_info:
        await OpenAICompatibleClient(settings()).generate_suggestions("doc", "src", "md")
    assert exc_info.value.status_code == 401
    assert len(calls) == 1


async def test_response_without_json_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda r: chat_response("Извините, не могу помочь."))
    with pytest.raises(LLMInvalidResponseError, match="нет JSON"):
        await OpenAICompatibleClient(settings()).generate_suggestions("doc", "src", "md")


async def test_response_without_choices_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda r: httpx.Response(200, json={"error": "model not found"}))
    with pytest.raises(LLMInvalidResponseError, match="choices"):
        await OpenAICompatibleClient(settings()).generate_suggestions("doc", "src", "md")


async def test_health_check_uses_models_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, lambda r: httpx.Response(200, json={"data": []}))
    assert await OpenAICompatibleClient(settings()).health_check() is True
    assert str(calls[0].url) == "http://llm.test/v1/models"
    assert calls[0].method == "GET"


async def test_health_check_false_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _install(monkeypatch, refuse)
    assert await OpenAICompatibleClient(settings()).health_check() is False


def test_extract_json_object_strips_fences_and_prose() -> None:
    raw = 'Ответ:\n```json\n{"suggestions": []}\n```\nСпасибо!'
    assert extract_json_object(raw) == '{"suggestions": []}'
    assert extract_json_object('{"a": 1}') == '{"a": 1}'
    with pytest.raises(LLMInvalidResponseError):
        extract_json_object("никакого json")
