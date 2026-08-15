import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.audit_log import AuditLog
from app.infrastructure.db.models.enums import AnalysisJobStatus, AuditAction, ChangeType, SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion


@pytest.mark.asyncio
async def test_analysis_job_queue_failure_updates_job_status(db_session):
    async with AsyncClient(app=app, base_url="http://test") as client:
        queue_failure_email = f"queue-failure-{uuid.uuid4().hex}@example.com"
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": queue_failure_email, "password": "password123"},
        )
        assert register_response.status_code == 201
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"email": queue_failure_email, "password": "password123"},
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        project_response = await client.post(
            "/api/v1/projects",
            json={"name": f"queue-test-{uuid.uuid4().hex}"},
            headers=headers,
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]

        files = {"file": ("document.txt", b"hello world", "text/plain")}
        document_response = await client.post(
            f"/api/v1/projects/{project_id}/documents",
            headers=headers,
            files=files,
        )
        assert document_response.status_code == 201
        document_id = document_response.json()["id"]

        with patch("app.api.v1.routers.analysis_jobs.run_analysis_job.delay", side_effect=RuntimeError("queue unavailable")):
            analysis_response = await client.post(
                f"/api/v1/projects/{project_id}/documents/{document_id}/analysis-jobs",
                headers=headers,
            )

        assert analysis_response.status_code == 201
        assert analysis_response.json()["status"] == "failed"
        assert analysis_response.json()["error_code"] == "QUEUE_UNAVAILABLE"

        result = await db_session.execute(
            select(AnalysisJob).where(AnalysisJob.document_id == uuid.UUID(document_id))
        )
        job = result.scalars().one()
        assert job.status == AnalysisJobStatus.FAILED
        assert job.error_code == "QUEUE_UNAVAILABLE"
        assert job.error_message is not None


@pytest.mark.asyncio
async def test_analysis_job_persists_celery_task_id(db_session):
    async with AsyncClient(app=app, base_url="http://test") as client:
        celery_task_id_email = f"celery-task-id-{uuid.uuid4().hex}@example.com"
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": celery_task_id_email, "password": "password123"},
        )
        assert register_response.status_code == 201
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"email": celery_task_id_email, "password": "password123"},
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        project_response = await client.post(
            "/api/v1/projects",
            json={"name": f"celery-task-id-test-{uuid.uuid4().hex}"},
            headers=headers,
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]

        files = {"file": ("document.txt", b"hello world", "text/plain")}
        document_response = await client.post(
            f"/api/v1/projects/{project_id}/documents",
            headers=headers,
            files=files,
        )
        assert document_response.status_code == 201
        document_id = document_response.json()["id"]

        fake_result = AsyncMock()
        fake_result.id = "fake-celery-id"

        with patch("app.api.v1.routers.analysis_jobs.run_analysis_job.delay", return_value=fake_result):
            analysis_response = await client.post(
                f"/api/v1/projects/{project_id}/documents/{document_id}/analysis-jobs",
                headers=headers,
            )

        assert analysis_response.status_code == 201
        result = await db_session.execute(
            select(AnalysisJob).where(AnalysisJob.document_id == uuid.UUID(document_id))
        )
        job = result.scalars().one()
        assert job.celery_task_id == "fake-celery-id"


@pytest.mark.asyncio
async def test_download_audit_log_uses_document_id_and_constraints(db_session):
    async with AsyncClient(app=app, base_url="http://test") as client:
        audit_download_email = f"audit-download-{uuid.uuid4().hex}@example.com"
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": audit_download_email, "password": "password123"},
        )
        assert register_response.status_code == 201
        user_id = register_response.json()["id"]
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"email": audit_download_email, "password": "password123"},
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        project_response = await client.post(
            "/api/v1/projects",
            json={"name": f"audit-download-test-{uuid.uuid4().hex}"},
            headers=headers,
        )
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]

        files = {"file": ("document.txt", b"hello world", "text/plain")}
        document_response = await client.post(
            f"/api/v1/projects/{project_id}/documents",
            headers=headers,
            files=files,
        )
        assert document_response.status_code == 201
        document_id = document_response.json()["id"]

        download_response = await client.get(
            f"/api/v1/projects/{project_id}/documents/{document_id}/download",
            headers=headers,
        )
        assert download_response.status_code == 200
        audit_query = await db_session.execute(
            select(AuditLog).where(AuditLog.document_id == uuid.UUID(document_id))
        )
        audit_row = audit_query.scalars().one()
        assert audit_row.action == AuditAction.DOWNLOAD
        assert audit_row.suggestion_id is None

        analysis_job = AnalysisJob(document_id=uuid.UUID(document_id))
        db_session.add(analysis_job)
        await db_session.commit()
        await db_session.refresh(analysis_job)

        suggestion = Suggestion(
            analysis_job_id=analysis_job.id,
            section_ref="section-1",
            change_type=ChangeType.EDIT,
            status=SuggestionStatus.PENDING,
        )
        db_session.add(suggestion)
        await db_session.commit()
        await db_session.refresh(suggestion)

        with pytest.raises(IntegrityError):
            await db_session.execute(
                AuditLog.__table__.insert().values(
                    user_id=uuid.UUID(user_id),
                    action=AuditAction.DOWNLOAD.value,
                    document_id=uuid.UUID(document_id),
                    suggestion_id=suggestion.id,
                )
            )
            await db_session.commit()
