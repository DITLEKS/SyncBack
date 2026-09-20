"""
Групповой запуск анализа по всем документам проекта.

POST /projects/{project_id}/documents/analysis-jobs/bulk

Переписан с нуля — старый файл использовал несуществующие символы
(AnalysisJobRead, BulkAnalysisJobsResponse, AnalysisJobRepository,
DocumentRepository импортировались из несуществующих мест).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_allowed_project
from app.api.schemas.analysis_job import AnalysisJobResponse
from app.core.dependencies import get_analysis_job_service
from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.domain.services.analysis_job_service import AnalysisJobService
from app.infrastructure.db.models.project import Project
from app.workers.celery_app import celery_app
from app.workers.tasks.analysis_tasks import run_analysis_job

router = APIRouter(
    prefix="/projects/{project_id}/documents",
    tags=["analysis-jobs"],
)


class BulkJobResult(BaseModel):
    document_id: uuid.UUID
    job: AnalysisJobResponse | None = None
    error: str | None = None


class BulkAnalysisJobsResponse(BaseModel):
    started: int
    skipped: int
    results: list[BulkJobResult]


@router.post(
    "/analysis-jobs/bulk",
    response_model=BulkAnalysisJobsResponse,
    status_code=status.HTTP_200_OK,
)
async def bulk_start_analysis_jobs(
    project: Project = Depends(get_allowed_project),
    service: AnalysisJobService = Depends(get_analysis_job_service),
) -> BulkAnalysisJobsResponse:
    """Запускает analysis_job для каждого документа проекта со статусом draft.
    Документы в in_progress / awaiting_approval / ready пропускаются без ошибки.

    Ответ содержит:
    - started  — кол-во новых jobs
    - skipped  — кол-во пропущенных документов
    - results  — детали по каждому документу
    """
    raw_results = await service.bulk_create_jobs_for_project(project.id)

    results: list[BulkJobResult] = []
    started = 0
    skipped = 0

    for item in raw_results:
        doc_id: uuid.UUID = item["document_id"]
        job = item.get("job")
        err: str | None = item.get("error")

        if err is not None:
            # Пропущен из-за статуса или уже запущен
            results.append(BulkJobResult(document_id=doc_id, error=err))
            skipped += 1
            continue

        # Диспатч в Celery
        try:
            task = run_analysis_job.delay(str(job.id))
            job = await service.mark_dispatched(job, task.id)
        except Exception as exc:  # noqa: BLE001
            job = await service.mark_job_queue_unavailable(job, str(exc))

        results.append(BulkJobResult(document_id=doc_id, job=AnalysisJobResponse.model_validate(job)))
        started += 1

    return BulkAnalysisJobsResponse(started=started, skipped=skipped, results=results)
