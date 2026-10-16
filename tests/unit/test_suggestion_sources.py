"""Unit tests for suggestion source provenance."""

import uuid
from datetime import UTC, datetime

from app.api.schemas.suggestion import SuggestionResponse
from app.infrastructure.db.models.enums import ChangeType, SourceType, SuggestionStatus
from app.infrastructure.db.models.source import Source
from app.infrastructure.db.models.suggestion import Suggestion


def _suggestion() -> Suggestion:
    return Suggestion(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        analysis_job_id=uuid.uuid4(),
        section_ref="Сроки",
        change_type=ChangeType.MODIFY,
        original_text="Срок — 10 дней.",
        suggested_text="Срок — 5 дней.",
        status=SuggestionStatus.PENDING,
        created_at=datetime.now(UTC),
    )


def test_response_serializes_multiple_sources() -> None:
    suggestion = _suggestion()
    suggestion.sources = [
        Source(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            name="Регламент",
            type=SourceType.FILE,
            storage_key="sources/regulation.docx",
        ),
        Source(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            name="Публичная политика",
            type=SourceType.URL,
            url="https://example.com/policy",
        ),
    ]

    payload = SuggestionResponse.model_validate(suggestion)

    assert [source.name for source in payload.sources] == ["Регламент", "Публичная политика"]
    assert payload.sources[0].type == "file"
    assert payload.sources[1].url == "https://example.com/policy"


def test_response_supports_legacy_suggestion_without_sources() -> None:
    suggestion = _suggestion()
    suggestion.sources = []

    assert SuggestionResponse.model_validate(suggestion).sources == []
