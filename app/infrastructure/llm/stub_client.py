from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem


def _first_line(text: str) -> str | None:
    """Первая непустая строка документа — якорь для добавления заглушки."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None


class StubLLMClient:
    async def generate_suggestions(
        self, document_text: str, source_text: str, document_format: str
    ) -> LLMSuggestionBatch:
        # Добавление без якоря маппер отбросит, поэтому якоримся на первую строку документа.
        item = LLMSuggestionItem(
            section_ref="Раздел 1",
            change_type="add",
            old_text=_first_line(document_text),
            new_text="Пример правки от stub-LLM (LLM_PROVIDER=stub) — замените на реальный вызов внешнего провайдера.",
            rationale="Заглушка: настоящая модель объясняет здесь причину правки.",
            confidence_score=0.5,
        )
        return LLMSuggestionBatch(items=[item], raw_response="stub")

    async def health_check(self) -> bool:
        return True
