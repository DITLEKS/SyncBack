"""
Реализация LLMClient для любого внешнего HTTP-провайдера инференса — конкретный вендор
не зафиксирован, endpoint/ключ настраиваются только через LLM_ENDPOINT/LLM_API_KEY в .env.
"""
import httpx
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.domain.exceptions import LLMInvalidResponseError
from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem
from app.infrastructure.llm.http_retry_mixin import HttpConnectionRetryMixin
from app.infrastructure.llm.schemas import LLMHttpSuggestionResponse
from app.workers.pipeline.llm_prompt_builder import build_prompt


class HttpLLMClient(HttpConnectionRetryMixin):
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    async def generate_suggestions(self, document_text: str, source_text: str, document_format: str) -> LLMSuggestionBatch:
        prompt = build_prompt(document_text, source_text, document_format)
        response = await self._post_with_connection_retry(
            url=self._settings.llm_endpoint,
            headers={"Authorization": f"Bearer {self._settings.llm_api_key}"},
            json_payload={"prompt": prompt},
            timeout_seconds=self._settings.llm_timeout_seconds,
        )
        try:
            parsed = LLMHttpSuggestionResponse.model_validate_json(response.text)
        except ValidationError as exc:
            raise LLMInvalidResponseError(f"LLM вернула ответ, не соответствующий ожидаемой схеме: {exc}") from exc
        items = [
            LLMSuggestionItem(section_ref=item.section_ref, change_type=item.change_type, old_text=item.old_text, new_text=item.new_text)
            for item in parsed.suggestions
        ]
        return LLMSuggestionBatch(items=items, raw_response=response.text)

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self._settings.llm_endpoint}/health")
                return response.status_code == 200
        except httpx.HTTPError:
            return False
