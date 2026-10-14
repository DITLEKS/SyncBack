"""Маппер отбрасывает добавления без якоря или с якорем, которого нет в документе."""

import uuid

import pytest

from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem
from app.infrastructure.db.models.enums import ChangeType
from app.workers.pipeline.suggestion_mapper import map_to_suggestions

DOCUMENT = "# Сроки\nСрок ответа — 10 дней.\nОплата картой.\n"


def _item(change_type: str, old_text: str | None, new_text: str | None = "Новый текст."):
    return LLMSuggestionItem(
        section_ref="Сроки",
        change_type=change_type,
        old_text=old_text,
        new_text=new_text,
        rationale=None,
        confidence_score=0.9,
    )


def _map(*items: LLMSuggestionItem):
    batch = LLMSuggestionBatch(items=list(items), raw_response="{}")
    return map_to_suggestions(batch, uuid.uuid4(), uuid.uuid4(), document_text=DOCUMENT)


def test_add_with_anchor_from_document_is_kept() -> None:
    result = _map(_item("add", "Срок ответа — 10 дней."))
    assert len(result) == 1
    assert result[0].change_type is ChangeType.ADD
    assert result[0].original_text == "Срок ответа — 10 дней."


@pytest.mark.parametrize("anchor", [None, "", "   "])
def test_add_without_anchor_is_dropped(anchor: str | None) -> None:
    assert _map(_item("add", anchor)) == []


def test_add_with_anchor_missing_from_document_is_dropped() -> None:
    assert _map(_item("add", "Такого абзаца в документе нет.")) == []


def test_anchor_is_matched_after_strip() -> None:
    result = _map(_item("add", "  Оплата картой.\n"))
    assert len(result) == 1


def test_modify_and_delete_are_not_filtered_by_anchor_rule() -> None:
    result = _map(_item("modify", "Нет в документе"), _item("delete", None, None))
    assert [s.change_type for s in result] == [ChangeType.MODIFY, ChangeType.DELETE]


def test_unknown_change_type_is_still_dropped() -> None:
    result = _map(_item("rename", "Оплата картой."), _item("add", "Оплата картой."))
    assert [s.change_type for s in result] == [ChangeType.ADD]
