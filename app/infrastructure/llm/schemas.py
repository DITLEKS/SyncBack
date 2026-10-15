"""Схема JSON с правками, который модель возвращает в choices[0].message.content.

Схема не зависит от провайдера: её задаёт промпт (app/infrastructure/llm/prompt.py),
а любая OpenAI-совместимая модель лишь заполняет поля.
"""

from pydantic import BaseModel, field_validator


class LLMHttpSuggestionItem(BaseModel):
    section_ref: str
    change_type: str
    old_text: str | None = None
    new_text: str | None = None
    # Для add: дословный фрагмент документа, после которого вставить new_text.
    insert_after: str | None = None
    rationale: str | None = None
    confidence_score: float | None = None

    @field_validator("confidence_score")
    @classmethod
    def _drop_out_of_range_confidence(cls, value: float | None) -> float | None:
        # Неверная оценка уверенности не повод отбрасывать весь ответ модели.
        if value is None or 0 <= value <= 1:
            return value
        return None


class LLMHttpSuggestionResponse(BaseModel):
    suggestions: list[LLMHttpSuggestionItem]
