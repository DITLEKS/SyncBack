"""
Unit-тесты статусных переходов документа по таблице переходов из спецификации UI.

Проверяются только доменные инварианты без обращения к БД или очереди.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    InvalidDocumentStatusError,
    ReviewNotCompleteError,
)
from app.domain.services.analysis_job_service import AnalysisJobService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.enums import (
    AnalysisJobStatus,
    DocumentStatus,
    SuggestionStatus,
)


def _make_document(status: DocumentStatus, job_id: uuid.UUID | None = None):
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.project_id = uuid.uuid4()
    doc.status = status
    doc.current_analysis_job_id = job_id
    return doc


def _make_job(status: AnalysisJobStatus = AnalysisJobStatus.PENDING):
    job = MagicMock()
    job.id = uuid.uuid4()
    job.status = status
    return job


# ---------------------------------------------------------------------------
# Переход №2: DRAFT → (очередь успешна) → IN_PROGRESS
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_job_from_draft_succeeds():
    doc = _make_document(DocumentStatus.DRAFT)
    job_repo = AsyncMock()
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc
    job_repo.get_active_by_document_id.return_value = None
    created_job = _make_job()
    job_repo.create_for_document.return_value = created_job

    service = AnalysisJobService(job_repo, doc_repo)
    result = await service.create_job(doc.project_id, doc.id)
    assert result is created_job


# ---------------------------------------------------------------------------
# Переход №9: AWAITING_APPROVAL → (пользователь запустил повторный анализ) → IN_PROGRESS
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_job_from_awaiting_approval_succeeds():
    """Переход №9: повторный запуск анализа из AWAITING_APPROVAL."""
    doc = _make_document(DocumentStatus.AWAITING_APPROVAL)
    job_repo = AsyncMock()
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc
    job_repo.get_active_by_document_id.return_value = None
    # update_status сбрасывает документ в DRAFT перед созданием job
    draft_doc = _make_document(DocumentStatus.DRAFT)
    draft_doc.id = doc.id
    draft_doc.project_id = doc.project_id
    doc_repo.update_status.return_value = draft_doc
    created_job = _make_job()
    job_repo.create_for_document.return_value = created_job

    service = AnalysisJobService(job_repo, doc_repo)
    result = await service.create_job(doc.project_id, doc.id)

    # Должен был сбросить в DRAFT перед постановкой
    doc_repo.update_status.assert_called_once_with(doc, DocumentStatus.DRAFT)
    assert result is created_job


# ---------------------------------------------------------------------------
# Запрос из READY — запрещён (конечный статус в MVP)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_job_from_ready_raises():
    doc = _make_document(DocumentStatus.READY)
    job_repo = AsyncMock()
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc

    service = AnalysisJobService(job_repo, doc_repo)
    with pytest.raises(InvalidDocumentStatusError):
        await service.create_job(doc.project_id, doc.id)


# ---------------------------------------------------------------------------
# Запрос из IN_PROGRESS — запрещён (и активная задача уже есть)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_job_from_in_progress_raises():
    doc = _make_document(DocumentStatus.IN_PROGRESS)
    job_repo = AsyncMock()
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc

    service = AnalysisJobService(job_repo, doc_repo)
    with pytest.raises(InvalidDocumentStatusError):
        await service.create_job(doc.project_id, doc.id)


# ---------------------------------------------------------------------------
# Переход №8: AWAITING_APPROVAL + все решения приняты → READY
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_finalize_review_moves_to_ready():
    job_id = uuid.uuid4()
    doc = _make_document(DocumentStatus.AWAITING_APPROVAL, job_id=job_id)
    suggestion_repo = AsyncMock()
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc
    suggestion_repo.count_by_analysis_job_and_status.return_value = 0  # нет PENDING
    ready_doc = _make_document(DocumentStatus.READY)
    doc_repo.update_status.return_value = ready_doc

    service = SuggestionService(suggestion_repo, doc_repo)
    result = await service.finalize_review(doc.project_id, doc.id)
    doc_repo.update_status.assert_called_once_with(doc, DocumentStatus.READY)
    assert result is ready_doc


# ---------------------------------------------------------------------------
# Finalize блокируется, если есть нерассмотренные правки
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_finalize_review_blocked_when_pending_exist():
    job_id = uuid.uuid4()
    doc = _make_document(DocumentStatus.AWAITING_APPROVAL, job_id=job_id)
    suggestion_repo = AsyncMock()
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc
    suggestion_repo.count_by_analysis_job_and_status.return_value = 3  # есть PENDING

    service = SuggestionService(suggestion_repo, doc_repo)
    with pytest.raises(ReviewNotCompleteError):
        await service.finalize_review(doc.project_id, doc.id)


# ---------------------------------------------------------------------------
# Finalize разрешён, если все правки отклонены (0 PENDING)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_finalize_review_allowed_when_all_rejected():
    """Правило: если пользователь отклонил все правки, документ всё равно → READY."""
    job_id = uuid.uuid4()
    doc = _make_document(DocumentStatus.AWAITING_APPROVAL, job_id=job_id)
    suggestion_repo = AsyncMock()
    doc_repo = AsyncMock()
    doc_repo.get_by_id.return_value = doc
    suggestion_repo.count_by_analysis_job_and_status.return_value = 0  # все отклонены
    ready_doc = _make_document(DocumentStatus.READY)
    doc_repo.update_status.return_value = ready_doc

    service = SuggestionService(suggestion_repo, doc_repo)
    result = await service.finalize_review(doc.project_id, doc.id)
    assert result is ready_doc
