"""Unit tests for suggestion source references."""

import uuid
from datetime import UTC, datetime

import pytest

from app.api.schemas.suggestion import SuggestionResponse
from app.infrastructure.db.models.source import Source
from app.infrastructure.db.models.suggestion import Suggestion


def test_suggestion_response_serializes_multiple_sources() -> None:
    suggestion = Suggestion(
        id=uuid.uuid4(), document_id=uuid.uuid4(), analysis_job_id=uuid.uuid4(), seq=1,
        section_ref="Общие положения", change_type="replace", original_text="Старый текст",
        suggested_text="Новый текст", status="pending", created_at=datetime.now(UTC),
    )
    suggestion.sources = [
        Source(id=uuid.uuid4(), project_id=uuid.uuid4(), name="Политика безопасности", source_type="file"),
        Source(id=uuid.uuid4(), project_id=uuid.uuid4(), name="Стандарт MFA", source_type="url", url="https://example.com/mfa"),
    ]

    payload = SuggestionResponse.model_validate(suggestion)

    assert [source.name for source in payload.sources] == ["Политика безопасности", "Стандарт MFA"]
    assert payload.sources[1].url == "https://example.com/mfa"


def test_suggestion_response_defaults_to_empty_sources() -> None:
    suggestion = Suggestion(
        id=uuid.uuid4(), document_id=uuid.uuid4(), analysis_job_id=uuid.uuid4(), seq=1,
        section_ref="Раздел", change_type="insert", original_text="", suggested_text="Текст",
        status="pending", created_at=datetime.now(UTC),
    )
    suggestion.sources = []

    payload = SuggestionResponse.model_validate(suggestion)

    assert payload.sources == []


@pytest.mark.asyncio
async def test_merge_duplicates_keeps_all_sources() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.infrastructure.db.repositories.suggestion_repository import SuggestionRepository

    first_source = SimpleNamespace(id=uuid.uuid4())
    second_source = SimpleNamespace(id=uuid.uuid4())
    common = dict(section_ref="Общие положения", change_type="replace", original_text="Старый текст", suggested_text="Новый текст", block_id=None, start_offset=None, end_offset=None)
    first = SimpleNamespace(id=uuid.uuid4(), sources=[first_source], **common)
    duplicate = SimpleNamespace(id=uuid.uuid4(), sources=[second_source], **common)
    session = SimpleNamespace(delete=AsyncMock(), flush=AsyncMock())
    repository = SuggestionRepository(session)
    repository.list_by_analysis_job = AsyncMock(return_value=[first, duplicate])  # type: ignore[method-assign]

    merged = await repository.merge_duplicates_by_analysis_job(uuid.uuid4())

    assert merged == 1
    assert [source.id for source in first.sources] == [first_source.id, second_source.id]
    session.delete.assert_awaited_once_with(duplicate)
