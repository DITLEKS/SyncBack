from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem


class StubLLMClient:
    async def generate_suggestions(self, document_text: str, source_text: str, document_format: str) -> LLMSuggestionBatch:
        item = LLMSuggestionItem(section_ref="Раздел 1", change_type="add", old_text=None, new_text="Пример правки от stub-LLM (LLM_PROVIDER=stub) — замените на реальный вызов внешнего провайдера.")
        return LLMSuggestionBatch(items=[item], raw_response="stub")

    async def health_check(self) -> bool:
        return True
