"""Маппер отбрасывает добавления без якоря или с якорем, которого нет в документе,
и пишет причины отброса в текст лога."""

import logging
import uuid

import pytest

from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem
from app.infrastructure.db.models.enums import ChangeType
from app.workers.pipeline.suggestion_mapper import map_to_suggestions

DOCUMENT = "# Сроки\nСрок ответа — 10 дней.\nОплата картой.\n"
LOGGER = "syncscribe.workers.suggestion_mapper"


def _item(change_type: str, old_text: str | None, new_text: str | None = "Новый текст."):
    return LLMSuggestionItem(
        section_ref="Сроки",
        change_type=change_type,
        old_text=old_text,
        new_text=new_text,
        rationale=None,
        confidence_score=0.9,
    )


def _map(*items: LLMSuggestionItem, raw_response: str = "{}"):
    batch = LLMSuggestionBatch(items=list(items), raw_response=raw_response)
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


def test_skip_reasons_and_items_are_in_log_message(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = _map(
            _item("add", "Оплата картой."),
            _item("add", "такого текста нет"),
            _item("add", None),
        )
    assert len(result) == 1
    messages = [record.getMessage() for record in caplog.records]
    summary = next(m for m in messages if m.startswith("Часть правок LLM отброшена"))
    assert "add_anchor_not_in_document=1" in summary
    assert "add_without_anchor=1" in summary
    assert "сохранено 1 из 3" in summary
    assert any("[add_anchor_not_in_document]" in m and "такого текста нет" in m for m in messages)
    assert not any("Сырой ответ" in m for m in messages)


def test_raw_response_is_logged_only_when_everything_is_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = '{"suggestions": [{"change_type": "add", "old_text": "нет"}]}'
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = _map(_item("add", "такого текста нет"), raw_response=raw)
    assert result == []
    messages = [record.getMessage() for record in caplog.records]
    assert any(m.startswith("Все 1 правок LLM отброшены") and raw in m for m in messages)
