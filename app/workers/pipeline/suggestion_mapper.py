"""Перевод ответа LLM в ORM-правки анализа."""

import logging
import uuid

from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem
from app.infrastructure.db.models.enums import ChangeType, SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion

logger = logging.getLogger("syncscribe.workers.suggestion_mapper")

_CHANGE_TYPE_MAP = {"add": ChangeType.ADD, "modify": ChangeType.MODIFY, "delete": ChangeType.DELETE}
_OLD_TEXT_LOG_LIMIT = 120
_RAW_RESPONSE_LOG_LIMIT = 4000


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


def _short(text: str | None, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


def _log_skipped(
    batch: LLMSuggestionBatch,
    skipped_items: list[tuple[str, LLMSuggestionItem]],
    *,
    kept: int,
    analysis_job_id: uuid.UUID,
    document_id: uuid.UUID,
) -> None:
    """Причины отброса — в тексте сообщения: консольный форматтер воркера не печатает extra.

    Сырой ответ модели логируется только когда отброшено всё: это единственный способ понять,
    что именно вернула модель, а при частичном отбросе достаточно построчного списка.
    """
    skipped: dict[str, int] = {}
    for reason, _ in skipped_items:
        skipped[reason] = skipped.get(reason, 0) + 1
    context = {
        "analysis_job_id": str(analysis_job_id),
        "document_id": str(document_id),
        "skipped": skipped,
        "kept": kept,
    }
    details = ", ".join(f"{reason}={count}" for reason, count in sorted(skipped.items()))
    logger.warning(
        "Часть правок LLM отброшена как невалидные: %s (сохранено %d из %d)",
        details,
        kept,
        len(batch.items),
        extra=context,
    )
    for reason, item in skipped_items:
        logger.warning(
            "Отброшена правка [%s] %s / раздел %r / old_text: %r",
            reason,
            item.change_type,
            item.section_ref,
            _short(item.old_text, _OLD_TEXT_LOG_LIMIT),
            extra=context,
        )
    if kept == 0:
        logger.warning(
            "Все %d правок LLM отброшены. Сырой ответ модели: %s",
            len(batch.items),
            _short(batch.raw_response, _RAW_RESPONSE_LOG_LIMIT),
            extra=context,
        )


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
    skipped_items: list[tuple[str, LLMSuggestionItem]] = []
    for item in batch.items:
        reason = _skip_reason(item, document_text)
        if reason is not None:
            skipped_items.append((reason, item))
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
    if skipped_items:
        _log_skipped(
            batch,
            skipped_items,
            kept=len(suggestions),
            analysis_job_id=analysis_job_id,
            document_id=document_id,
        )
    return suggestions
