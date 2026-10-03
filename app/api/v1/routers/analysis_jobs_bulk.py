"""Групповой запуск анализа по всем документам проекта.

POST /projects/{project_id}/documents/analysis-jobs/bulk

#10: файл переписан с нуля. Старые несуществующие символы — удалены.

UI-fix: добавлен опциональный document_ids в Body —
  позволяет запустить анализ только для выбранных документов (если есть чекбоксы).
  Если document_ids=null/опущено — запустить все analyzable документы проекта.

C-2 (аудит): статус-код зависит от результата.
  - 201 Created  — если started > 0 (хотя бы одна задача создана)
  - 200 OK       — если started == 0 (все пропущены, ничего не создано)

REFACTOR: dispatch делегирован в service.dispatch_job() —
  роутер не импортирует Celery-задачи напрямую.
"""

import uuid

from fastapi import APIRouter, Body, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import get_allowed_project
from app.api.schemas.analysis_job import AnalysisJobResponse
from app.core.dependencies import get_analysis_job_service
from app.domain.services.analysis_job_service import AnalysisJobService
from app.infrastructure.db.models.project import Project

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

    document_ids: list[uuid.UUID] | None = None


@router.post(
    "/analysis-jobs/bulk",
    responses={
        201: {
            "model": BulkAnalysisJobsResponse,
            "description": "Одна или несколько задач успешно созданы (started > 0)",
        },
        200: {
            "model": BulkAnalysisJobsResponse,
            "description": "Ни одна задача не создана — все документы пропущены (started == 0)",
        },
    },
)
async def bulk_start_analysis_jobs(
    payload: BulkAnalysisRequest = Body(default=BulkAnalysisRequest()),
    project: Project = Depends(get_allowed_project),
    service: AnalysisJobService = Depends(get_analysis_job_service),
) -> JSONResponse:
    """POST body (опционально):

    ```json
    { "document_ids": ["uuid1", "uuid2"] }
    ```

    Без body (или document_ids=null) — запускает анализ для всех analyzable документов проекта.
    Документы в in_progress / awaiting_approval / ready пропускаются без ошибки.

    C-2: возвращает 201 если started > 0, иначе 200.
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
        else:
            job_schema = AnalysisJobResponse.model_validate(job)
            results.append(BulkJobResult(document_id=doc_id, job=job_schema))
            started += 1
            # REFACTOR: dispatch делегирован в сервис — роутер не знает о Celery
            try:
                await service.dispatch_job(job)
            except Exception:  # noqa: BLE001
                pass  # задача создана; dispatcher-failure не блокирует ответ

    response_body = BulkAnalysisJobsResponse(
        started=started,
        skipped=skipped,
        results=results,
    )

    http_status = status.HTTP_201_CREATED if started > 0 else status.HTTP_200_OK
    return JSONResponse(
        status_code=http_status,
        content=response_body.model_dump(mode="json"),
    )
