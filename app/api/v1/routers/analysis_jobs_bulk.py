from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, get_project_and_check_owner
from app.api.schemas.analysis_job import AnalysisJobRead, BulkAnalysisJobsResponse
from app.domain.exceptions import (
    AnalysisAlreadyRunningError,
    DocumentNotFoundError,
    InvalidDocumentStatusError,
)
from app.domain.services.analysis_job_service import AnalysisJobService
from app.infrastructure.db.session import get_async_session

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["analysis_jobs"])


@router.post("/{document_id}/analysis-jobs", response_model=AnalysisJobRead, status_code=status.HTTP_201_CREATED)
async def create_analysis_job(
    project_id: str,
    document_id: str,
    current_user = Depends(get_current_user),
    session = Depends(get_async_session),
):
    project = await get_project_and_check_owner(session, project_id, current_user.id)
    service = AnalysisJobService(
        analysis_job_repository=AnalysisJobRepository(session),
        document_repository=DocumentRepository(session),
    )
    try:
        job = await service.create_job(project.id, uuid.UUID(document_id))
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidDocumentStatusError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except AnalysisAlreadyRunningError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return AnalysisJobRead.from_orm(job)


@router.post("/analysis-jobs/bulk", response_model=BulkAnalysisJobsResponse)
async def bulk_create_analysis_jobs(
    project_id: str,
    current_user = Depends(get_current_user),
    session = Depends(get_async_session),
):
    project = await get_project_and_check_owner(session, project_id, current_user.id)
    service = AnalysisJobService(
        analysis_job_repository=AnalysisJobRepository(session),
        document_repository=DocumentRepository(session),
    )
    results = await service.bulk_create_jobs_for_project(project.id)
    return BulkAnalysisJobsResponse.from_results(results)
