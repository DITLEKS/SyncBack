import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    InvalidDocumentStatusError,
    ReviewNotCompleteError,
)
from app.domain.services.analysis_job_service import AnalysisJobService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.enums import DocumentStatus


@pytest.mark.asyncio
async def test_analysis_requires_draft_document():
    document = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), status=DocumentStatus.READY)
    documents = SimpleNamespace(get_by_id=AsyncMock(return_value=document))
    jobs = SimpleNamespace(get_active_by_document_id=AsyncMock())
    service = AnalysisJobService(jobs, documents)
    with pytest.raises(InvalidDocumentStatusError):
        await service.create_job(document.project_id, document.id)


@pytest.mark.asyncio
async def test_second_active_analysis_is_rejected():
    document = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), status=DocumentStatus.DRAFT)
    documents = SimpleNamespace(get_by_id=AsyncMock(return_value=document))
    jobs = SimpleNamespace(get_active_by_document_id=AsyncMock(return_value=object()))
    service = AnalysisJobService(jobs, documents)
    with pytest.raises(AnalysisAlreadyRunningError):
        await service.create_job(document.project_id, document.id)


@pytest.mark.asyncio
async def test_review_cannot_finalize_with_pending_suggestions():
    document = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status=DocumentStatus.AWAITING_APPROVAL,
        current_analysis_job_id=uuid.uuid4(),
    )
    documents = SimpleNamespace(get_by_id=AsyncMock(return_value=document), update_status=AsyncMock())
    suggestions = SimpleNamespace(count_by_analysis_job_and_status=AsyncMock(return_value=2))
    service = SuggestionService(suggestions, documents)
    with pytest.raises(ReviewNotCompleteError):
        await service.finalize_review(document.project_id, document.id)


@pytest.mark.asyncio
async def test_review_finalization_marks_document_ready():
    document = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status=DocumentStatus.AWAITING_APPROVAL,
        current_analysis_job_id=uuid.uuid4(),
    )
    ready = SimpleNamespace(status=DocumentStatus.READY)
    documents = SimpleNamespace(
        get_by_id=AsyncMock(return_value=document), update_status=AsyncMock(return_value=ready)
    )
    suggestions = SimpleNamespace(count_by_analysis_job_and_status=AsyncMock(return_value=0))
    service = SuggestionService(suggestions, documents)
    result = await service.finalize_review(document.project_id, document.id)
    assert result.status == DocumentStatus.READY
    documents.update_status.assert_awaited_once_with(document, DocumentStatus.READY)
