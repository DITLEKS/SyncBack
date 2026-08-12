"""Запуск и просмотр задач анализа документа."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_allowed_project
from app.api.schemas.analysis_job import AnalysisJobResponse
from app.core.dependencies import get_analysis_job_service
from app.domain.exceptions import DocumentNotFoundError
from app.domain.services.analysis_job_service import AnalysisJobService
from app.infrastructure.db.models.project import Project
from app.workers.tasks.analysis_tasks import run_analysis_job

router = APIRouter(prefix="/projects/{project_id}/documents/{document_id}/analysis-jobs", tags=["analysis-jobs"])


@router.post("", response_model=AnalysisJobResponse, status_code=status.HTTP_201_CREATED)
async def start_analysis_job(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    analysis_job_service: AnalysisJobService = Depends(get_analysis_job_service),
) -> AnalysisJobResponse:
    try:
        job = await analysis_job_service.create_job(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    run_analysis_job.delay(str(job.id))
    return AnalysisJobResponse.model_validate(job)


@router.get("/{job_id}", response_model=AnalysisJobResponse)
async def get_analysis_job(
    document_id: uuid.UUID,
    job_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    analysis_job_service: AnalysisJobService = Depends(get_analysis_job_service),
) -> AnalysisJobResponse:
    try:
        job = await analysis_job_service.get_job(project.id, document_id, job_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return AnalysisJobResponse.model_validate(job)
