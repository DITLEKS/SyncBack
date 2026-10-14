"""Перевод ответа LLM в ORM-правки анализа."""

import logging
import uuid

from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem
from app.infrastructure.db.models.enums import ChangeType, SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion

logger = logging.getLogger("syncscribe.workers.suggestion_mapper")

_CHANGE_TYPE_MAP = {"add": ChangeType.ADD, "modify": ChangeType.MODIFY, "delete": ChangeType.DELETE}


def _skip_reason(item: LLMSuggestionItem, document_text: str) -> str | None:
    """Почему правку нельзя сохранить; None — если можно.

    Добавление без якоря не сохраняется: без него текст негде разместить, а выбирать место
    за модель (конец раздела, конец документа) мы не должны. Якорь, которого нет в документе
    дословно, — та же ошибка модели: экспорт такую правку всё равно не применит.
    """
    if item.change_type not in _CHANGE_TYPE_MAP:
        return "unknown_change_type"
    if item.change_type != "add":
        return None
    anchor = (item.old_text or "").strip()
    if not anchor:
        return "add_without_anchor"
    if anchor not in document_text:
        return "add_anchor_not_in_document"
    return None


def map_to_suggestions(
    batch: LLMSuggestionBatch,
    analysis_job_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    document_text: str,
) -> list[Suggestion]:
    """Невалидные элементы пропускаются с предупреждением в логе: лучше потерять одну правку,
    чем уронить обработку источника целиком."""
    suggestions: list[Suggestion] = []
    skipped: dict[str, int] = {}
    for item in batch.items:
        reason = _skip_reason(item, document_text)
        if reason is not None:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        suggestions.append(
            Suggestion(
                analysis_job_id=analysis_job_id,
                document_id=document_id,
                section_ref=item.section_ref,
                change_type=_CHANGE_TYPE_MAP[item.change_type],
                status=SuggestionStatus.PENDING,
                original_text=item.old_text,
                suggested_text=item.new_text,
                rationale=item.rationale,
                confidence_score=item.confidence_score,
            )
        )
    if skipped:
        logger.warning(
            "Часть правок LLM отброшена как невалидные",
            extra={
                "analysis_job_id": str(analysis_job_id),
                "document_id": str(document_id),
                "skipped": skipped,
                "kept": len(suggestions),
            },
        )
    return suggestions
