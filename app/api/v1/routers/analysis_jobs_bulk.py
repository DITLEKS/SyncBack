"""
Групповой запуск анализа по всем документам проекта.

POST /projects/{project_id}/documents/analysis-jobs/bulk

#10: файл переписан с нуля. Старые несуществующие символы—удалены.

UI-fix: добавлен опциональный document_ids в Body —
  позволяет запустить анализ только для выбранных документов (если есть чекбоксы).
  Если document_ids=null/опущено — запустить все analyzable документы проекта.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, status
from pydantic import BaseModel

from app.api.deps import get_allowed_project
from app.api.schemas.analysis_job import AnalysisJobResponse
from app.core.dependencies import get_analysis_job_service
from app.domain.services.analysis_job_service import AnalysisJobService
from app.infrastructure.db.models.project import Project
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


class BulkAnalysisRequest(BaseModel):
    """UI-fix: если document_ids задан — анализ запускается только для них.
    Если null или поле опущено — запустить все analyzable документы проекта.
    """
    document_ids: Optional[list[uuid.UUID]] = None


@router.post(
    "/analysis-jobs/bulk",
    response_model=BulkAnalysisJobsResponse,
    status_code=status.HTTP_200_OK,
)
async def bulk_start_analysis_jobs(
    payload: BulkAnalysisRequest = Body(default=BulkAnalysisRequest()),
    project: Project = Depends(get_allowed_project),
    service: AnalysisJobService = Depends(get_analysis_job_service),
) -> BulkAnalysisJobsResponse:
    """POST body (опционально):

    ```json
    { "document_ids": ["uuid1", "uuid2"] }
    ```

    Без body (или document_ids=null) — запускает анализ для всех analyzable документов проекта.
    Документы в in_progress / awaiting_approval / ready пропускаются без ошибки.
    """
    raw_results = await service.bulk_create_jobs_for_project(
        project.id,
        document_ids=payload.document_ids,
    )

    results: list[BulkJobResult] = []
    started = 0
    skipped = 0

    for item in raw_results:
        doc_id: uuid.UUID = item["document_id"]
        job = item.get("job")
        err: str | None = item.get("error")

        if err is not None:
            results.append(BulkJobResult(document_id=doc_id, error=err))
            skipped += 1
            continue

        try:
            task = run_analysis_job.delay(str(job.id))
            job = await service.mark_dispatched(job, task.id)
        except Exception as exc:  # noqa: BLE001
            job = await service.mark_job_queue_unavailable(job, str(exc))

        results.append(
            BulkJobResult(
                document_id=doc_id,
                job=AnalysisJobResponse.model_validate(job),
            )
        )
        started += 1

    return BulkAnalysisJobsResponse(started=started, skipped=skipped, results=results)
