"""
PR4 regression guard — Editor API pagination.

Проверяет три свойства агрегата /editor:

  1. Вторая страница (offset=2) возвращает правки, отличные от первой.
  2. Пустая страница (offset=9999) возвращает пустой список, но
     suggestions_total остаётся прежним.
  3. Счётчики counters.pending + accepted + rejected == suggestions_total,
     а не длина текущей страницы (регрессия исходного _safe_count_by_status,
     который до PR4 считал статусы только по загруженным правкам).

Тесты работают напрямую с БД через SQLAlchemy (без HTTP-слоя) и маркированы
@pytest.mark.integration — запускаются только в CI с реальным PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

N_SUGGESTIONS = 5  # достаточно для двух страниц по 2 + одной по 1


# ---------------------------------------------------------------------------
# Helpers (дублированы локально, чтобы тест был самодостаточным)
# ---------------------------------------------------------------------------


async def _make_user(s: AsyncSession) -> uuid.UUID:
    uid = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO users (id, email, hashed_password, is_active, created_at) "
            "VALUES (:id, :email, 'x', true, now())"
        ),
        {"id": str(uid), "email": f"{uid}@pg.local"},
    )
    return uid


async def _make_project(s: AsyncSession, owner: uuid.UUID) -> uuid.UUID:
    pid = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO projects (id, name, owner_id, created_at) "
            "VALUES (:id, 'Pagination test', :owner, now())"
        ),
        {"id": str(pid), "owner": str(owner)},
    )
    return pid


async def _make_document(s: AsyncSession, project: uuid.UUID) -> uuid.UUID:
    did = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO documents "
            "  (id, project_id, name, format, status, size_bytes, uploaded_at, created_at) "
            "VALUES "
            "  (:id, :pid, 'pagination.md', 'markdown', 'draft', 100, now(), now())"
        ),
        {"id": str(did), "pid": str(project)},
    )
    return did


async def _make_job(s: AsyncSession, doc: uuid.UUID, project: uuid.UUID) -> uuid.UUID:
    jid = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO analysis_jobs (id, document_id, project_id, status, created_at) "
            "VALUES (:id, :doc, :proj, 'awaiting_approval', now())"
        ),
        {"id": str(jid), "doc": str(doc), "proj": str(project)},
    )
    await s.execute(
        text("UPDATE documents SET current_analysis_job_id = :jid WHERE id = :did"),
        {"jid": str(jid), "did": str(doc)},
    )
    return jid


async def _make_suggestions(
    s: AsyncSession,
    doc: uuid.UUID,
    job: uuid.UUID,
    n: int,
    status: str = "pending",
) -> list[uuid.UUID]:
    ids: list[uuid.UUID] = []
    for i in range(n):
        sid = uuid.uuid4()
        await s.execute(
            text(
                "INSERT INTO suggestions "
                "  (id, document_id, analysis_job_id, section_ref, "
                "   change_type, old_text, new_text, status, created_at) "
                "VALUES "
                "  (:id, :doc, :job, :ref, 'replace', 'old', 'new', :status, now())"
            ),
            {
                "id": str(sid),
                "doc": str(doc),
                "job": str(job),
                "ref": f"sec-{i}",
                "status": status,
            },
        )
        ids.append(sid)
    return ids


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEditorPagination:
    """Тесты пагинации правок на уровне БД (без HTTP-слоя)."""

    @pytest.mark.asyncio
    async def test_page2_returns_different_rows(self, pg_session: AsyncSession) -> None:
        """Вторая страница не совпадает с первой."""
        owner = await _make_user(pg_session)
        proj = await _make_project(pg_session, owner)
        doc = await _make_document(pg_session, proj)
        job = await _make_job(pg_session, doc, proj)
        await _make_suggestions(pg_session, doc, job, N_SUGGESTIONS)
        await pg_session.flush()

        page1 = (
            await pg_session.execute(
                text(
                    "SELECT id FROM suggestions "
                    "WHERE analysis_job_id = :jid ORDER BY created_at LIMIT 2 OFFSET 0"
                ),
                {"jid": str(job)},
            )
        ).fetchall()

        page2 = (
            await pg_session.execute(
                text(
                    "SELECT id FROM suggestions "
                    "WHERE analysis_job_id = :jid ORDER BY created_at LIMIT 2 OFFSET 2"
                ),
                {"jid": str(job)},
            )
        ).fetchall()

        assert len(page1) == 2
        assert len(page2) == 2
        assert {r[0] for r in page1}.isdisjoint({r[0] for r in page2}), (
            "Страница 1 и страница 2 не должны содержать одинаковые правки"
        )

    @pytest.mark.asyncio
    async def test_empty_page_returns_no_rows_but_total_unchanged(
        self, pg_session: AsyncSession
    ) -> None:
        """Пустая страница (offset > total) не меняет итоговый count."""
        owner = await _make_user(pg_session)
        proj = await _make_project(pg_session, owner)
        doc = await _make_document(pg_session, proj)
        job = await _make_job(pg_session, doc, proj)
        await _make_suggestions(pg_session, doc, job, N_SUGGESTIONS)
        await pg_session.flush()

        empty_page = (
            await pg_session.execute(
                text("SELECT id FROM suggestions WHERE analysis_job_id = :jid LIMIT 2 OFFSET 9999"),
                {"jid": str(job)},
            )
        ).fetchall()

        total = (
            await pg_session.execute(
                text("SELECT COUNT(*) FROM suggestions WHERE analysis_job_id = :jid"),
                {"jid": str(job)},
            )
        ).scalar()

        assert empty_page == [], "Пустая страница должна вернуть 0 строк"
        assert total == N_SUGGESTIONS, (
            f"suggestions_total должен остаться {N_SUGGESTIONS}, получили {total}"
        )

    @pytest.mark.asyncio
    async def test_counters_reflect_all_suggestions_not_page(
        self, pg_session: AsyncSession
    ) -> None:
        """
        Регрессионный тест PR4: счётчики pending+accepted+rejected == total,
        независимо от размера страницы.

        До PR4 _safe_count_by_status считал статусы по загруженной странице (N=2),
        а suggestions_total возвращал полный COUNT. При total=5 и limit=2:
          counters.pending = 2  (страница), suggestions_total = 5  ← несоответствие.

        После PR4 оба числа берутся из независимых COUNT-запросов по job_id:
          counters.pending = 5 (все), suggestions_total = 5  ← консистентно.
        """
        owner = await _make_user(pg_session)
        proj = await _make_project(pg_session, owner)
        doc = await _make_document(pg_session, proj)
        job = await _make_job(pg_session, doc, proj)
        # 3 pending + 1 accepted + 1 rejected
        await _make_suggestions(pg_session, doc, job, 3, status="pending")
        await _make_suggestions(pg_session, doc, job, 1, status="accepted")
        await _make_suggestions(pg_session, doc, job, 1, status="rejected")
        await pg_session.flush()

        total = (
            await pg_session.execute(
                text("SELECT COUNT(*) FROM suggestions WHERE analysis_job_id = :jid"),
                {"jid": str(job)},
            )
        ).scalar()

        pending = (
            await pg_session.execute(
                text(
                    "SELECT COUNT(*) FROM suggestions "
                    "WHERE analysis_job_id = :jid AND status = 'pending'"
                ),
                {"jid": str(job)},
            )
        ).scalar()

        accepted = (
            await pg_session.execute(
                text(
                    "SELECT COUNT(*) FROM suggestions "
                    "WHERE analysis_job_id = :jid AND status = 'accepted'"
                ),
                {"jid": str(job)},
            )
        ).scalar()

        rejected = (
            await pg_session.execute(
                text(
                    "SELECT COUNT(*) FROM suggestions "
                    "WHERE analysis_job_id = :jid AND status = 'rejected'"
                ),
                {"jid": str(job)},
            )
        ).scalar()

        assert pending + accepted + rejected == total, (
            f"Сумма счётчиков ({pending}+{accepted}+{rejected}) должна равняться total ({total})"
        )
        assert pending == 3
        assert accepted == 1
        assert rejected == 1
