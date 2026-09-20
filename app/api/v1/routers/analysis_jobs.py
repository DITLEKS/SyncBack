"""Запуск, просмотр и отмена задач анализа документа.

P0-7: POST принимает опциональный заголовок Idempotency-Key.
Если для данного документа уже существует job с тем же ключом
(хранится в job.idempotency_key), возвращаем существующий job
со статусом HTTP 200 вместо создания дубля.

OpenAPI: объявлены responses для 200 (idempotent) и 201 (created),
оба используют одну схему AnalysisJobResponse.
"""

import uuid
from contextlib import suppress

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from app.api.deps import get_allowed_project
from app.api.schemas.analysis_job import AnalysisJobResponse
from app.core.dependencies import get_analysis_job_service
from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    AnalysisJobNotCancellableError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.domain.services.analysis_job_service import AnalysisJobService
from app.infrastructure.db.models.project import Project
from app.workers.celery_app import celery_app
from app.workers.tasks.analysis_tasks import run_analysis_job

router = APIRouter(
    prefix="/projects/{project_id}/documents/{document_id}/analysis-jobs", tags=["analysis-jobs"]
)

_JOB_RESPONSE_SCHEMA = AnalysisJobResponse


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"model": AnalysisJobResponse, "description": "Задача создана"},
        200: {"model": AnalysisJobResponse, "description": "Идемпотентный запрос — задача уже существует"},
    },
)
async def start_analysis_job(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    service: AnalysisJobService = Depends(get_analysis_job_service),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),  # P0-7
) -> JSONResponse:
    # P0-7: идемпотентный повторный запрос — вернуть существующий job (HTTP 200)
    if idempotency_key:
        existing = await service.find_job_by_idempotency_key(
            project.id, document_id, idempotency_key
        )
        if existing is not None:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=jsonable_encoder(AnalysisJobResponse.model_validate(existing)),
            )

    try:
        job = await service.create_job(
            project.id, document_id, idempotency_key=idempotency_key
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (AnalysisAlreadyRunningError, InvalidDocumentStatusError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        task = run_analysis_job.delay(str(job.id))
    except Exception as exc:
        job = await service.mark_job_queue_unavailable(job, str(exc))
        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content=jsonable_encoder(AnalysisJobResponse.model_validate(job)),
        )
    job = await service.mark_dispatched(job, task.id)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=jsonable_encoder(AnalysisJobResponse.model_validate(job)),
    )


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
    if job.celery_task_id:
        with suppress(Exception):
            celery_app.control.revoke(job.celery_task_id, terminate=False)
    return AnalysisJobResponse.model_validate(job)
