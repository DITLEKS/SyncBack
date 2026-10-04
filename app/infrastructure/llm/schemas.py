"""
ВАЖНО: это условный дефолт-плейсхолдер контракта, а не подтверждённая спецификация
какого-либо конкретного вендора. Реальный провайдер раннего инференса пока не определён.
"""

from pydantic import BaseModel, field_validator


class LLMHttpSuggestionItem(BaseModel):
    section_ref: str
    change_type: str
    old_text: str | None = None
    new_text: str | None = None
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
