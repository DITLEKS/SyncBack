"""Запуск, просмотр и отмена задач анализа документа.

P0-7: Идемпотентный Idempotency-Key.
P0-9: Повторный анализ READY-документа требует force=True в теле запроса.
      Без force — HTTP 409 с confirmation_required=True.

ОПТИМИЗАЦИЯ (код-ревью):
- #4  detail HTTPException — .model_dump() вместо jsonable_encoder на Pydantic-объекте.
- #9  _job_response() — хелпер вместо трёх одинаковых JSONResponse-блоков.

N-2 (ревью): убран прямой импорт celery_app из роутера.
  Отзыв Celery-задачи делегирован в AnalysisJobService.revoke_celery_task().
  Роутер больше не зависит от инфраструктуры Celery напрямую.

N-4 (ревью): убран импорт DocumentStatus (ORM-enum из инфраструктуры).
  Сравнение статуса перенесено внутрь сервисного метода get_document_for_job(),
  где сессия гарантированно открыта (N-5). Роутер получает простой bool.
"""

import uuid
from contextlib import suppress

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.deps import get_allowed_project
from app.api.schemas.analysis_job import (
    AnalysisJobConflictResponse,
    AnalysisJobCreateRequest,
    AnalysisJobResponse,
)
from app.core.dependencies import get_analysis_job_service
from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    AnalysisJobNotCancellableError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.domain.services.analysis_job_service import AnalysisJobService
from app.infrastructure.db.models.project import Project
from app.workers.tasks.analysis_tasks import run_analysis_job

router = APIRouter(
    prefix="/projects/{project_id}/documents/{document_id}/analysis-jobs",
    tags=["analysis-jobs"],
)


# #9 Хелпер, чтобы не дублировать JSONResponse + jsonable_encoder в трёх местах
def _job_response(job, http_status: int = status.HTTP_201_CREATED) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content=jsonable_encoder(AnalysisJobResponse.model_validate(job)),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"model": AnalysisJobResponse, "description": "Задача создана"},
        200: {"model": AnalysisJobResponse, "description": "Идемпотентный запрос — задача уже существует"},
        409: {
            "model": AnalysisJobConflictResponse,
            "description": (
                "Анализ уже запущен, или документ READY — нужен force=True (#9)"
            ),
        },
    },
)
async def start_analysis_job(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    service: AnalysisJobService = Depends(get_analysis_job_service),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    body: AnalysisJobCreateRequest = AnalysisJobCreateRequest(),
) -> JSONResponse:
    # P0-7: идемпотентный повторный запрос
    if idempotency_key:
        existing = await service.find_job_by_idempotency_key(
            project.id, document_id, idempotency_key
        )
        if existing is not None:
            return _job_response(existing, status.HTTP_200_OK)  # #9

    # P0-9: повторный анализ документа в статусе READY без force.
    # N-5: проверка статуса выполняется внутри сессии сервиса (get_document_for_job),
    #      роутер получает только bool — никакого доступа к ORM-атрибутам за пределами сессии.
    try:
        is_ready = await service.check_document_is_ready(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if is_ready and not body.force:
        # #4 model_dump() вместо jsonable_encoder на ещё несериализованном Pydantic-объекте
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=AnalysisJobConflictResponse(
                detail=(
                    "Документ уже в статусе Готов. "
                    "Перезапустить анализ? Передайте force=true."
                ),
                confirmation_required=True,
            ).model_dump(),
        )

    try:
        job = await service.create_job(
            project.id, document_id, idempotency_key=idempotency_key
        )
    except (AnalysisAlreadyRunningError, InvalidDocumentStatusError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        task = run_analysis_job.delay(str(job.id))
    except Exception as exc:  # noqa: BLE001
        job = await service.mark_job_queue_unavailable(job, str(exc))
        return _job_response(job)  # #9

    job = await service.mark_dispatched(job, task.id)
    return _job_response(job)  # #9


@router.get("/{job_id}", response_model=AnalysisJobResponse)
async def get_analysis_job(
    document_id: uuid.UUID,
    job_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    service: AnalysisJobService = Depends(get_analysis_job_service),
) -> AnalysisJobResponse:
    try:
        job = await service.get_job(project.id, document_id, job_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AnalysisJobResponse.model_validate(job)


@router.post("/{job_id}/cancel", response_model=AnalysisJobResponse)
async def cancel_analysis_job(
    document_id: uuid.UUID,
    job_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    service: AnalysisJobService = Depends(get_analysis_job_service),
) -> AnalysisJobResponse:
    try:
        job = await service.cancel_job(project.id, document_id, job_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AnalysisJobNotCancellableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # N-2: revoke делегирован в сервис — роутер не знает о celery_app напрямую
    if job.celery_task_id:
        with suppress(Exception):
            await service.revoke_celery_task(job.celery_task_id)
    return AnalysisJobResponse.model_validate(job)
