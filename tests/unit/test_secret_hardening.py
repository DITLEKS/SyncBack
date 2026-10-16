"""Регрессии: секреты и пользовательский текст не попадают в ошибки и логи."""

from __future__ import annotations

import json
import logging
import sys
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging_setup import CorrelationJsonFormatter, PlainCorrelationFormatter
from app.domain.exceptions import LLMInvalidResponseError
from app.infrastructure.llm.auth import GigaChatOAuth, NoAuth
from app.infrastructure.llm.http_retry_mixin import HttpConnectionRetryMixin
from app.infrastructure.llm.openai_compatible_client import OpenAICompatibleClient

pytestmark = pytest.mark.asyncio

SECRETS = {
    "dsn_password": "db-password-never-log",
    "api_key": "api-key-never-log",
    "cookie": "session-cookie-never-log",
    "trace_secret": "trace-secret-never-log",
}


def _record_with_exception() -> logging.LogRecord:
    try:
        raise ValueError(f"client_secret={SECRETS['trace_secret']}")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        (
            "DATABASE_URL=postgresql://user:"
            f"{SECRETS['dsn_password']}@db/app "
            f"Authorization=Bearer {SECRETS['api_key']} "
            f"Cookie=session={SECRETS['cookie']}"
        ),
        (),
        exc_info,
    )
    record.postgres_password = "structured-password-never-log"
    record.context = {"llm_api_key": "nested-api-key-never-log"}
    return record


@pytest.mark.parametrize("formatter", [CorrelationJsonFormatter(), PlainCorrelationFormatter()])
def test_formatters_redact_messages_structured_fields_and_tracebacks(formatter) -> None:
    rendered = formatter.format(_record_with_exception())
    for secret in (*SECRETS.values(), "structured-password-never-log", "nested-api-key-never-log"):
        assert secret not in rendered
    assert "***REDACTED***" in rendered


_BASE_SETTINGS = {
    "ENV": "production",
    "DATABASE_URL": "postgresql+asyncpg://app:db-strong-password-8391@db/app",
    "REDIS_URL": "redis://redis:6379/0",
    "S3_ENDPOINT": "seaweedfs:8333",
    "S3_ACCESS_KEY": "app-access",
    "S3_SECRET_KEY": "s3-strong-secret-8391",
    "S3_BUCKET": "bucket",
    "JWT_SECRET": "jwt-strong-secret-8391-jwt-strong-secret-8391",
    "CORS_ALLOWED_ORIGINS": "https://app.example.test",
}


@pytest.mark.parametrize(
    "override",
    [
        {"DATABASE_URL": "postgresql+asyncpg://syncscribe:syncscribe@db/app"},
        {"S3_SECRET_KEY": "change-me-s3-app-secret"},
        {"JWT_SECRET": "change-me-change-me-change-me-change-me-0000"},
        {
            "LLM_PROVIDER": "openai_compatible",
            "LLM_ENDPOINT": "https://llm.example.test/v1",
            "LLM_MODEL": "model",
            "LLM_AUTH": "bearer",
            "LLM_API_KEY": "change-me-llm-key",
        },
    ],
)
def test_deployed_environments_reject_placeholder_credentials(override: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="placeholder/стандартные"):
        Settings(_env_file=None, **({**_BASE_SETTINGS, **override}))  # type: ignore[arg-type]


def test_local_environment_keeps_example_credentials_usable() -> None:
    settings = Settings(
        _env_file=None,
        **{
            **_BASE_SETTINGS,
            "ENV": "local",
            "DATABASE_URL": "postgresql+asyncpg://syncscribe:syncscribe@db/app",
            "S3_SECRET_KEY": "change-me-s3-app-secret",
            "JWT_SECRET": "change-me-change-me-change-me-change-me-0000",
        },
    )
    assert settings.env == "local"


class _RejectedClient(HttpConnectionRetryMixin):
    def _make_client(self, timeout: float, verify: bool | str = True) -> httpx.AsyncClient:
        marker = "provider-body-never-log"
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(400, text=marker))
        )


async def test_llm_http_error_does_not_include_provider_body() -> None:
    marker = "provider-body-never-log"
    with pytest.raises(LLMInvalidResponseError) as exc_info:
        await _RejectedClient()._post_with_retries(
            url="https://llm.example.test/chat",
            headers={},
            json_payload={"prompt": "private-document"},
            timeout_seconds=1,
            max_retries=0,
        )
    assert marker not in str(exc_info.value)


async def test_oauth_error_does_not_include_provider_body() -> None:
    marker = "oauth-body-never-log"

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(401, text=marker))
        )

    oauth = GigaChatOAuth("auth-key", "scope", "https://oauth.example.test", factory)
    with pytest.raises(LLMInvalidResponseError) as exc_info:
        await oauth.headers()
    assert marker not in str(exc_info.value)


class _InvalidSchemaClient(OpenAICompatibleClient):
    async def _chat(self, payload: dict[str, object]) -> httpx.Response:
        marker = "user-document-fragment-never-log"
        content = json.dumps(
            {
                "suggestions": [
                    {
                        "section_ref": "section",
                        "change_type": "add",
                        "old_text": "anchor",
                        "new_text": "replacement",
                        "rationale": "reason",
                        "confidence_score": marker,
                    }
                ]
            }
        )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )


async def test_pydantic_input_is_not_in_llm_exception_or_cause() -> None:
    settings = cast(
        Settings,
        SimpleNamespace(llm_max_input_chars=1000, llm_model="model", llm_temperature=0.1),
    )
    client = _InvalidSchemaClient(settings=settings, auth=NoAuth())
    with pytest.raises(LLMInvalidResponseError) as exc_info:
        await client.generate_suggestions("private-document", "private-source", "txt")
    assert "user-document-fragment-never-log" not in str(exc_info.value)
    assert "confidence_score" in str(exc_info.value)
    assert exc_info.value.__cause__ is None
