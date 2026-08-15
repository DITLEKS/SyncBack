"""
ВАЖНО: это условный дефолт-плейсхолдер контракта, а не подтверждённая спецификация
какого-либо конкретного вендора. Реальный провайдер раннего инференса пока не определён.
"""
from pydantic import BaseModel


class LLMHttpSuggestionItem(BaseModel):
    section_ref: str
    change_type: str
    old_text: str | None = None
    new_text: str | None = None


class LLMHttpSuggestionResponse(BaseModel):
    suggestions: list[LLMHttpSuggestionItem]
