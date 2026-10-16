"""Проверка сохранения ссылки правки на использованный источник."""

import uuid

from app.domain.interfaces.llm_client import LLMSuggestionBatch, LLMSuggestionItem
from app.workers.pipeline.suggestion_mapper import map_to_suggestions


def test_mapper_assigns_source_id_to_each_suggestion() -> None:
    source_id = uuid.uuid4()
    batch = LLMSuggestionBatch(
        items=[
            LLMSuggestionItem(
                section_ref="Сроки",
                change_type="modify",
                old_text="10 дней",
                new_text="14 дней",
                rationale="Источник требует 14 дней.",
                confidence_score=0.95,
            )
        ],
        raw_response="{}",
    )

    suggestions = map_to_suggestions(
        batch,
        uuid.uuid4(),
        uuid.uuid4(),
        source_id=source_id,
        document_text="Срок ответа — 10 дней.",
        document_format="docx",
    )

    assert len(suggestions) == 1
    assert suggestions[0].source_id == source_id


def test_suggestion_source_response_includes_project_id() -> None:
    from app.api.schemas.suggestion import SuggestionSourceResponse

    project_id = uuid.uuid4()
    payload = SuggestionSourceResponse(
        id=uuid.uuid4(),
        project_id=project_id,
        name="Регламент безопасности",
        type="url",
        url="https://example.com/security-policy",
    ).model_dump(mode="json")

    assert payload["project_id"] == str(project_id)
