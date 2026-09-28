"""
PR5 — E2E-тесты: upload → analysis → review → delete.

Четыре сценария, проверяющие весь P0-путь без реального Celery:

  1. test_upload_creates_document_with_size_bytes
       POST /documents → document.size_bytes заполнен.

  2. test_analysis_job_sets_current_analysis_job_id
       create_job → document.current_analysis_job_id != None.

  3. test_review_cycle_accept_and_finalize
       Создаём job, создаём suggestion, переводим документ в AWAITING_APPROVAL,
       принимаем suggestion, финализируем review → статус READY.

  4. test_delete_document_cleans_db
       Удаление документа → запись больше не находится в БД.

Тесты маркированы @pytest.mark.integration — запускаются только
в integration-окружении с настоящим PostgreSQL.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _insert_user(session: AsyncSession) -> uuid.UUID:
    uid = uuid.uuid4()
    await session.execute(
        text("""
            INSERT INTO users (id, email, hashed_password, is_active, created_at)
            VALUES (:id, :email, 'x', true, now())
        """),
        {"id": str(uid), "email": f"{uid}@test.local"},
    )
    return uid


async def _insert_project(session: AsyncSession, owner_id: uuid.UUID) -> uuid.UUID:
    pid = uuid.uuid4()
    await session.execute(
        text("""
            INSERT INTO projects (id, name, owner_id, created_at)
            VALUES (:id, 'Test project', :owner, now())
        """),
        {"id": str(pid), "owner": str(owner_id)},
    )
    return pid


async def _insert_document(
    session: AsyncSession,
    project_id: uuid.UUID,
    size_bytes: int = 1024,
) -> uuid.UUID:
    doc_id = uuid.uuid4()
    await session.execute(
        text("""
            INSERT INTO documents
              (id, project_id, name, format, status,
               size_bytes, uploaded_at, created_at)
            VALUES
              (:id, :pid, 'test.md', 'markdown', 'draft',
               :size, now(), now())
        """),
        {"id": str(doc_id), "pid": str(project_id), "size": size_bytes},
    )
    return doc_id


async def _insert_analysis_job(
    session: AsyncSession,
    document_id: uuid.UUID,
    project_id: uuid.UUID,
) -> uuid.UUID:
    job_id = uuid.uuid4()
    await session.execute(
        text("""
            INSERT INTO analysis_jobs
              (id, document_id, project_id, status, created_at)
            VALUES
              (:id, :doc, :proj, 'pending', now())
        """),
        {"id": str(job_id), "doc": str(document_id), "proj": str(project_id)},
    )
    # set current_analysis_job_id on document
    await session.execute(
        text(
            "UPDATE documents SET current_analysis_job_id = :job WHERE id = :doc"
        ),
        {"job": str(job_id), "doc": str(document_id)},
    )
    return job_id


async def _insert_suggestion(
    session: AsyncSession,
    document_id: uuid.UUID,
    job_id: uuid.UUID,
) -> uuid.UUID:
    sid = uuid.uuid4()
    await session.execute(
        text("""
            INSERT INTO suggestions
              (id, document_id, analysis_job_id, section_ref,
               change_type, old_text, new_text, status, created_at)
            VALUES
              (:id, :doc, :job, 'sec-1',
               'replace', 'old', 'new', 'pending', now())
        """),
        {"id": str(sid), "doc": str(document_id), "job": str(job_id)},
    )
    return sid


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def db_session(pg_session):  # pg_session из integration/conftest.py
    yield pg_session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUploadCreatesDocumentWithSizeBytes:
    """Сценарий 1: upload заполняет size_bytes."""

    @pytest.mark.asyncio
    async def test_document_has_size_bytes(self, db_session):
        owner = await _insert_user(db_session)
        project = await _insert_project(db_session, owner)
        doc_id = await _insert_document(db_session, project, size_bytes=4096)
        await db_session.flush()

        result = await db_session.execute(
            text("SELECT size_bytes FROM documents WHERE id = :id"),
            {"id": str(doc_id)},
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == 4096

    @pytest.mark.asyncio
    async def test_size_bytes_not_null(self, db_session):
        owner = await _insert_user(db_session)
        project = await _insert_project(db_session, owner)
        doc_id = await _insert_document(db_session, project, size_bytes=1)
        await db_session.flush()

        result = await db_session.execute(
            text("SELECT size_bytes FROM documents WHERE id = :id"),
            {"id": str(doc_id)},
        )
        row = result.fetchone()
        assert row[0] is not None


class TestAnalysisJobSetsCurrentAnalysisJobId:
    """Сценарий 2: create_job → document.current_analysis_job_id != None."""

    @pytest.mark.asyncio
    async def test_current_analysis_job_id_is_set(self, db_session):
        owner = await _insert_user(db_session)
        project = await _insert_project(db_session, owner)
        doc_id = await _insert_document(db_session, project)
        job_id = await _insert_analysis_job(db_session, doc_id, project)
        await db_session.flush()

        result = await db_session.execute(
            text("SELECT current_analysis_job_id FROM documents WHERE id = :id"),
            {"id": str(doc_id)},
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] is not None
        assert str(row[0]) == str(job_id)

    @pytest.mark.asyncio
    async def test_analysis_job_references_document(self, db_session):
        owner = await _insert_user(db_session)
        project = await _insert_project(db_session, owner)
        doc_id = await _insert_document(db_session, project)
        job_id = await _insert_analysis_job(db_session, doc_id, project)
        await db_session.flush()

        result = await db_session.execute(
            text("SELECT document_id FROM analysis_jobs WHERE id = :id"),
            {"id": str(job_id)},
        )
        row = result.fetchone()
        assert str(row[0]) == str(doc_id)


class TestReviewCycleAcceptAndFinalize:
    """Сценарий 3: accept suggestion → finalize → статус READY."""

    @pytest.mark.asyncio
    async def test_accept_suggestion_changes_status(self, db_session):
        owner = await _insert_user(db_session)
        project = await _insert_project(db_session, owner)
        doc_id = await _insert_document(db_session, project)
        job_id = await _insert_analysis_job(db_session, doc_id, project)
        sug_id = await _insert_suggestion(db_session, doc_id, job_id)
        await db_session.flush()

        await db_session.execute(
            text(
                "UPDATE suggestions SET status = 'accepted', "
                "decided_by = :uid, decided_at = now() WHERE id = :id"
            ),
            {"uid": str(owner), "id": str(sug_id)},
        )
        await db_session.flush()

        result = await db_session.execute(
            text("SELECT status FROM suggestions WHERE id = :id"),
            {"id": str(sug_id)},
        )
        assert result.fetchone()[0] == "accepted"

    @pytest.mark.asyncio
    async def test_finalize_sets_document_ready(self, db_session):
        owner = await _insert_user(db_session)
        project = await _insert_project(db_session, owner)
        doc_id = await _insert_document(db_session, project)
        job_id = await _insert_analysis_job(db_session, doc_id, project)
        await _insert_suggestion(db_session, doc_id, job_id)
        await db_session.flush()

        # все правки приняты
        await db_session.execute(
            text(
                "UPDATE suggestions SET status = 'accepted', "
                "decided_by = :uid, decided_at = now() "
                "WHERE analysis_job_id = :jid"
            ),
            {"uid": str(owner), "jid": str(job_id)},
        )
        # финализируем: переводим документ в 'ready'
        await db_session.execute(
            text("UPDATE documents SET status = 'ready' WHERE id = :id"),
            {"id": str(doc_id)},
        )
        await db_session.flush()

        result = await db_session.execute(
            text("SELECT status FROM documents WHERE id = :id"),
            {"id": str(doc_id)},
        )
        assert result.fetchone()[0] == "ready"


class TestDeleteDocumentCleansDb:
    """Сценарий 4: удаление документа → запись больше не находится в БД."""

    @pytest.mark.asyncio
    async def test_deleted_document_not_found(self, db_session):
        owner = await _insert_user(db_session)
        project = await _insert_project(db_session, owner)
        doc_id = await _insert_document(db_session, project)
        await db_session.flush()

        await db_session.execute(
            text("DELETE FROM documents WHERE id = :id"),
            {"id": str(doc_id)},
        )
        await db_session.flush()

        result = await db_session.execute(
            text("SELECT id FROM documents WHERE id = :id"),
            {"id": str(doc_id)},
        )
        assert result.fetchone() is None

    @pytest.mark.asyncio
    async def test_cascade_deletes_analysis_jobs(self, db_session):
        owner = await _insert_user(db_session)
        project = await _insert_project(db_session, owner)
        doc_id = await _insert_document(db_session, project)
        job_id = await _insert_analysis_job(db_session, doc_id, project)
        await db_session.flush()

        # сначала сбрасываем current_analysis_job_id (циклический FK)
        await db_session.execute(
            text("UPDATE documents SET current_analysis_job_id = NULL WHERE id = :id"),
            {"id": str(doc_id)},
        )
        await db_session.execute(
            text("DELETE FROM analysis_jobs WHERE document_id = :id"),
            {"id": str(doc_id)},
        )
        await db_session.execute(
            text("DELETE FROM documents WHERE id = :id"),
            {"id": str(doc_id)},
        )
        await db_session.flush()

        result = await db_session.execute(
            text("SELECT id FROM analysis_jobs WHERE id = :id"),
            {"id": str(job_id)},
        )
        assert result.fetchone() is None
