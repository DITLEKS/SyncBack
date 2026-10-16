"""Универсальный клиент OpenAI-совместимых LLM API."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.domain.exceptions import LLMInvalidResponseError, LLMTimeoutError
from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem
from app.infrastructure.llm.auth import AuthStrategy, build_auth
from app.infrastructure.llm.http_retry_mixin import HttpConnectionRetryMixin
from app.infrastructure.llm.prompt import build_prompt
from app.infrastructure.llm.schemas import LLMHttpSuggestionResponse

logger = logging.getLogger("syncscribe.llm.openai_compatible")
_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def extract_json_object(text: str) -> str:
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMInvalidResponseError("В ответе LLM нет JSON-объекта с правками")
    return cleaned[start : end + 1]


def _validation_error_summary(exc: ValidationError) -> str:
    """Только пути и типы ошибок: input/context могут содержать текст документа."""
    details: list[str] = []
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    for error in errors[:10]:
        location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        details.append(f"{location} ({error.get('type', 'invalid')})")
    if len(errors) > 10:
        details.append(f"ещё {len(errors) - 10}")
    return ", ".join(details) or "неизвестная ошибка схемы"


class OpenAICompatibleClient(HttpConnectionRetryMixin):
    def __init__(self, settings: Settings | None = None, auth: AuthStrategy | None = None):
        self._settings = settings or get_settings()
        self._auth = auth or build_auth(self._settings, self._new_client)

    def _verify(self) -> bool | str:
        if not self._settings.llm_verify_ssl:
            return False
        return self._settings.llm_ca_bundle or True

    def _new_client(self) -> httpx.AsyncClient:
        return self._make_client(timeout=self._settings.llm_timeout_seconds, verify=self._verify())

    def _url(self, path: str) -> str:
        return f"{self._settings.llm_endpoint.rstrip('/')}/{path.lstrip('/')}"

    async def _chat(self, payload: dict[str, Any]) -> httpx.Response:
        url = self._url("chat/completions")
        headers = await self._auth.headers()
        try:
            return await self._post_with_retries(
                url=url,
                headers=headers,
                json_payload=payload,
                timeout_seconds=self._settings.llm_timeout_seconds,
                max_retries=self._settings.llm_max_retries,
                verify=self._verify(),
            )
        except LLMInvalidResponseError as exc:
            if not self._auth.refreshable or exc.status_code != 401:
                raise
        headers = await self._auth.headers(force_refresh=True)
        return await self._post_with_retries(
            url=url,
            headers=headers,
            json_payload=payload,
            timeout_seconds=self._settings.llm_timeout_seconds,
            max_retries=self._settings.llm_max_retries,
            verify=self._verify(),
        )

    async def generate_suggestions(
        self, document_text: str, source_text: str, document_format: str
    ) -> LLMSuggestionBatch:
        prompt = build_prompt(
            document_text,
            source_text,
            document_format,
            max_input_chars=self._settings.llm_max_input_chars,
        )
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._settings.llm_temperature,
            "stream": False,
        }
        response = await self._chat(payload)
        content = self._message_content(response)
        try:
            parsed = LLMHttpSuggestionResponse.model_validate_json(extract_json_object(content))
        except ValidationError as exc:
            summary = _validation_error_summary(exc)
            raise LLMInvalidResponseError(
                f"LLM вернула ответ, не соответствующий ожидаемой схеме: {summary}"
            ) from None
        items = [
            LLMSuggestionItem(
                section_ref=item.section_ref,
                change_type=item.change_type,
                old_text=item.old_text,
                new_text=item.new_text,
                rationale=item.rationale,
                confidence_score=item.confidence_score,
                insert_after=item.insert_after,
            )
            for item in parsed.suggestions
        ]
        return LLMSuggestionBatch(items=items, raw_response=content)

    @staticmethod
    def _message_content(response: httpx.Response) -> str:
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMInvalidResponseError(
                "LLM вернула ответ без choices[0].message.content"
            ) from exc
        if not isinstance(content, str):
            raise LLMInvalidResponseError("choices[0].message.content не строка")
        return content

    async def health_check(self) -> bool:
        try:
            headers = await self._auth.headers()
            async with self._make_client(timeout=5, verify=self._verify()) as client:
                response = await client.get(self._url("models"), headers=headers)
                return response.is_success
        except (httpx.HTTPError, LLMInvalidResponseError, LLMTimeoutError, OSError):
            logger.warning("Проверка доступности LLM не прошла", exc_info=True)
            return False
