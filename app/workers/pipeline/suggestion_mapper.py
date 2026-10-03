"""Перевод ответа LLM в ORM-правки анализа."""

import uuid

from app.domain.interfaces.llm_client import LLMSuggestionBatch
from app.infrastructure.db.models.enums import ChangeType, SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion

_CHANGE_TYPE_MAP = {"add": ChangeType.ADD, "modify": ChangeType.MODIFY, "delete": ChangeType.DELETE}


def map_to_suggestions(
    batch: LLMSuggestionBatch,
    analysis_job_id: uuid.UUID,
    document_id: uuid.UUID,
) -> list[Suggestion]:
    """Элементы с неизвестным change_type пропускаются: лучше потерять одну правку,
    чем уронить обработку источника целиком."""
    suggestions: list[Suggestion] = []
    for item in batch.items:
        change_type = _CHANGE_TYPE_MAP.get(item.change_type)
        if change_type is None:
            continue
        suggestions.append(
            Suggestion(
                analysis_job_id=analysis_job_id,
                document_id=document_id,
                section_ref=item.section_ref,
                change_type=change_type,
                status=SuggestionStatus.PENDING,
                original_text=item.old_text,
                suggested_text=item.new_text,
            )
        )
    return suggestions
