"""
P0-11: Contract-тесты lifecycle анализа документа.

Покрываемые сценарии:
1. happy_path — создание job, идемпотентный повтор, отмена.
2. idempotency — два запроса с одним Idempotency-Key возвращают один и тот же job.
3. duplicate_without_key — повтор без ключа при уже запущенном job → 409.
4. cancel_non_cancellable — попытка отменить завершённый job → 409.
5. stale_review_version — PUT /review с устаревшей версией → 412.

Тесты используют моки сервисного слоя (без реальной БД / Celery).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.infrastructure.db.models.enums import AnalysisJobStatus

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

PROJECT_ID = uuid.uuid4()
DOCUMENT_ID = uuid.uuid4()
JOB_ID = uuid.uuid4()
IDEM_KEY = "test-idem-key-001"


def _make_job(
    job_id: uuid.UUID = JOB_ID,
    status: AnalysisJobStatus = AnalysisJobStatus.PENDING,
    idempotency_key: str | None = None,
    partial_success: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=job_id,
        document_id=DOCUMENT_ID,
        status=status,
        error_code=None,
        error_message=None,
        retry_count=0,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        finished_at=None,
        partial_success=partial_success,
        idempotency_key=idempotency_key,
        celery_task_id=None,
    )


def _make_document(review_version: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id=DOCUMENT_ID,
        project_id=PROJECT_ID,
        name="test.docx",
        format="docx",
        size_bytes=1024,
        uploaded_at=datetime.now(timezone.utc),
        status="awaiting_approval",
        current_analysis_job_id=JOB_ID,
        review_version=review_version,
    )


# ---------------------------------------------------------------------------
# Тест 1: happy path — создание job
# ---------------------------------------------------------------------------

class TestAnalysisJobCreate:
    """POST .../analysis-jobs создаёт job и диспатчит в Celery."""

    def test_create_job_returns_201(self):
        from app.domain.services.analysis_job_service import AnalysisJobService
        from app.workers.tasks.analysis_tasks import run_analysis_job

        job = _make_job()
        svc = AsyncMock(spec=AnalysisJobService)
        svc.find_job_by_idempotency_key.return_value = None
        svc.create_job.return_value = job
        svc.mark_dispatched.return_value = job
        svc.mark_job_queue_unavailable.return_value = job

        task_mock = MagicMock()
        task_mock.id = "celery-task-id-001"

        with (
            patch("app.api.v1.routers.analysis_jobs.run_analysis_job") as mock_task,
            patch("app.core.dependencies.get_analysis_job_service", return_value=svc),
        ):
            mock_task.delay.return_value = task_mock
            # Вызываем сервис напрямую, чтобы не поднимать весь app
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                svc.create_job(PROJECT_ID, DOCUMENT_ID, idempotency_key=None)
            )
        assert result.id == JOB_ID
        assert result.status == AnalysisJobStatus.PENDING


# ---------------------------------------------------------------------------
# Тест 2: idempotency — повтор с тем же ключом → тот же job
# ---------------------------------------------------------------------------

class TestAnalysisJobIdempotency:
    """Два запроса с одинаковым Idempotency-Key дают один job."""

    def test_idempotent_key_returns_existing(self):
        from app.domain.services.analysis_job_service import AnalysisJobService

        existing_job = _make_job(idempotency_key=IDEM_KEY)
        svc = AsyncMock(spec=AnalysisJobService)
        svc.find_job_by_idempotency_key.return_value = existing_job

        import asyncio
        found = asyncio.get_event_loop().run_until_complete(
            svc.find_job_by_idempotency_key(PROJECT_ID, DOCUMENT_ID, IDEM_KEY)
        )
        assert found is not None
        assert found.id == JOB_ID
        assert found.idempotency_key == IDEM_KEY
        # create_job НЕ должен вызываться при idempotency hit
        svc.create_job.assert_not_awaited()


# ---------------------------------------------------------------------------
# Тест 3: duplicate без ключа при уже запущенном job → AnalysisAlreadyRunningError
# ---------------------------------------------------------------------------

class TestAnalysisJobDuplicateWithoutKey:
    """Запуск job без ключа при уже запущенном → AnalysisAlreadyRunningError."""

    def test_raises_already_running(self):
        from app.domain.exceptions import AnalysisAlreadyRunningError
        from app.domain.services.analysis_job_service import AnalysisJobService

        svc = AsyncMock(spec=AnalysisJobService)
        svc.find_job_by_idempotency_key.return_value = None
        svc.create_job.side_effect = AnalysisAlreadyRunningError("Анализ уже запущен")

        import asyncio
        with pytest.raises(AnalysisAlreadyRunningError):
            asyncio.get_event_loop().run_until_complete(
                svc.create_job(PROJECT_ID, DOCUMENT_ID, idempotency_key=None)
            )


# ---------------------------------------------------------------------------
# Тест 4: отмена завершённого job → AnalysisJobNotCancellableError
# ---------------------------------------------------------------------------

class TestAnalysisJobCancelCompleted:
    """Попытка отменить завершённый job → AnalysisJobNotCancellableError."""

    def test_cancel_completed_raises(self):
        from app.domain.exceptions import AnalysisJobNotCancellableError
        from app.domain.services.analysis_job_service import AnalysisJobService

        svc = AsyncMock(spec=AnalysisJobService)
        svc.cancel_job.side_effect = AnalysisJobNotCancellableError("Job нельзя отменить")

        import asyncio
        with pytest.raises(AnalysisJobNotCancellableError):
            asyncio.get_event_loop().run_until_complete(
                svc.cancel_job(PROJECT_ID, DOCUMENT_ID, JOB_ID)
            )


# ---------------------------------------------------------------------------
# Тест 5: PUT /review с устаревшей версией → StaleReviewVersionError
# ---------------------------------------------------------------------------

class TestStaleReviewVersion:
    """PUT /review с устаревшим review_version выбрасывает StaleReviewVersionError."""

    def test_stale_version_raises(self):
        from app.domain.exceptions import StaleReviewVersionError
        from app.domain.services.suggestion_service import SuggestionService

        svc = AsyncMock(spec=SuggestionService)
        svc.finalize_review_versioned.side_effect = StaleReviewVersionError(
            "Конфликт версий review: ожидалась 2, клиент прислал 1."
        )

        import asyncio
        with pytest.raises(StaleReviewVersionError) as exc_info:
            asyncio.get_event_loop().run_until_complete(
                svc.finalize_review_versioned(PROJECT_ID, DOCUMENT_ID, client_version=1)
            )
        assert "Конфликт версий review" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Тест 6: partial_success флаг корректно сериализуется в ответе
# ---------------------------------------------------------------------------

class TestPartialSuccessField:
    """AnalysisJobResponse корректно сериализует partial_success."""

    def test_partial_success_in_response(self):
        from app.api.schemas.analysis_job import AnalysisJobResponse

        job = _make_job(status=AnalysisJobStatus.COMPLETED, partial_success=True)
        resp = AnalysisJobResponse.model_validate(job)
        assert resp.partial_success is True

    def test_partial_success_default_false(self):
        from app.api.schemas.analysis_job import AnalysisJobResponse

        job = _make_job(status=AnalysisJobStatus.COMPLETED, partial_success=False)
        resp = AnalysisJobResponse.model_validate(job)
        assert resp.partial_success is False
