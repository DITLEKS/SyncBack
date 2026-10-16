"""Перевод ответа LLM в ORM-правки анализа."""

import logging
import uuid
from dataclasses import replace

from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem
from app.infrastructure.db.models.enums import ChangeType, SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion

logger = logging.getLogger("syncscribe.workers.suggestion_mapper")
_CHANGE_TYPE_MAP = {"add": ChangeType.ADD, "modify": ChangeType.MODIFY, "delete": ChangeType.DELETE}
_SINGLE_PARAGRAPH_FORMATS = frozenset({"docx", "doc"})


def _heading_text(line: str) -> str:
    return line.strip().lstrip("#").strip().strip("*").strip()


def infer_anchor(document_text: str, section_ref: str) -> str | None:
    wanted = _heading_text(section_ref or "")
    if not wanted:
        return None
    lines = document_text.split("\n")
    for index, line in enumerate(lines):
        if _heading_text(line) != wanted:
            continue
        for candidate in lines[index + 1 :]:
            if candidate.strip():
                return candidate.strip()
        return line.strip()
    return None


def _last_paragraph(text: str) -> str:
    paragraphs = [line.strip() for line in text.split("\n") if line.strip()]
    return paragraphs[-1] if paragraphs else ""


def _normalize(item: LLMSuggestionItem) -> LLMSuggestionItem:
    if item.change_type != "modify":
        return item
    old = (item.old_text or "").strip()
    new = (item.new_text or "").strip()
    if old and not new:
        return replace(item, change_type="delete", new_text=None)
    if old and len(new) > len(old) and new.startswith(old):
        return replace(
            item,
            change_type="add",
            old_text="",
            new_text=new[len(old) :].strip(),
            insert_after=_last_paragraph(old),
        )
    return item


def _resolve_anchor(item: LLMSuggestionItem, document_text: str) -> tuple[LLMSuggestionItem, bool]:
    if item.change_type != "add":
        return item, False
    anchor = (item.insert_after or item.old_text or "").strip()
    if anchor:
        return replace(item, old_text=anchor, insert_after=None), False
    inferred = infer_anchor(document_text, item.section_ref)
    return replace(item, old_text=inferred, insert_after=None), inferred is not None


def _skip_reason(
    item: LLMSuggestionItem, document_text: str, *, single_paragraph: bool
) -> str | None:
    if item.change_type not in _CHANGE_TYPE_MAP:
        return "unknown_change_type"
    if item.change_type == "add":
        anchor = (item.old_text or "").strip()
        if not anchor:
            return "add_without_anchor"
        if anchor not in document_text:
            return "add_anchor_not_in_document"
        return None
    if single_paragraph and "\n" in (item.old_text or "").strip():
        return "old_text_spans_paragraphs"
    return None


def _with_inferred_note(rationale: str | None, section_ref: str) -> str:
    note = f"Место вставки выбрано автоматически: после первого абзаца раздела «{section_ref}»."
    return f"{rationale.rstrip()} {note}" if rationale and rationale.strip() else note


def _log_skipped(
    batch: LLMSuggestionBatch,
    skipped_items: list[tuple[str, LLMSuggestionItem]],
    *,
    kept: int,
    analysis_job_id: uuid.UUID,
    document_id: uuid.UUID,
) -> None:
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
    if kept == 0:
        logger.warning(
            "Все %d правок LLM отброшены (job=%s, document=%s)",
            len(batch.items),
            analysis_job_id,
            document_id,
            extra=context,
        )


def map_to_suggestions(
    batch: LLMSuggestionBatch,
    analysis_job_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    source_id: uuid.UUID | None = None,
    document_text: str,
    document_format: str = "",
) -> list[Suggestion]:
    single_paragraph = document_format.lower() in _SINGLE_PARAGRAPH_FORMATS
    suggestions: list[Suggestion] = []
    skipped_items: list[tuple[str, LLMSuggestionItem]] = []
    inferred_count = 0
    appended_count = 0
    for raw_item in batch.items:
        normalized = _normalize(raw_item)
        if raw_item.change_type == "modify" and normalized.change_type == "add":
            appended_count += 1
        item, inferred = _resolve_anchor(normalized, document_text)
        reason = _skip_reason(item, document_text, single_paragraph=single_paragraph)
        if reason is not None:
            skipped_items.append((reason, item))
            continue
        rationale = item.rationale
        if inferred:
            inferred_count += 1
            rationale = _with_inferred_note(rationale, item.section_ref)
        suggestions.append(
            Suggestion(
                analysis_job_id=analysis_job_id,
                document_id=document_id,
                source_id=source_id,
                section_ref=item.section_ref,
                change_type=_CHANGE_TYPE_MAP[item.change_type],
                status=SuggestionStatus.PENDING,
                original_text=item.old_text,
                suggested_text=item.new_text,
                rationale=rationale,
                confidence_score=item.confidence_score,
            )
        )
    log_context = {"analysis_job_id": str(analysis_job_id), "document_id": str(document_id)}
    if appended_count:
        logger.info(
            "Для %d правок modify с дописанным текстом тип изменён на add",
            appended_count,
            extra=log_context,
        )
    if inferred_count:
        logger.info(
            "Для %d добавлений якорь выбран автоматически (первый абзац раздела)",
            inferred_count,
            extra=log_context,
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
