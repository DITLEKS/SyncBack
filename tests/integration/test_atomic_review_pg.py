"""
PostgreSQL-уровень: конкурентные CAS-тесты для atomic_review_save.

Запуск через testcontainers (не нужен .env.test):
  pip install testcontainers[postgres] asyncpg
  pytest -m integration tests/integration/test_atomic_review_pg.py

Почему не SELECT ... FOR UPDATE:
  DocumentRepository.compare_and_increment_review_version использует
    UPDATE documents SET review_version = review_version + 1
    WHERE id = :id AND review_version = :expected
    RETURNING id, ...
  Это атомарный CAS на уровне строки PostgreSQL: один UPDATE автоматически
  блокирует строку до завершения транзакции, второй UPDATE читает
  уже обновлённые данные и получает 0 строк.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

pytestmark = pytest.mark.integration  # скипается в обычном режиме

# ---------------------------------------------------------------------------
# testcontainers — запускаем Postgres-контейнер один раз на всю сессию
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pg_container():
    """Docker-контейнер с PostgreSQL 16 (запускается один раз на сессию)."""
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("Требуется: pip install testcontainers[postgres] asyncpg")

    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest_asyncio.fixture(scope="session")
async def pg_engine(pg_container):
    """AsyncEngine подключён к testcontainer; все таблицы создаются через Base.metadata."""
    from app.infrastructure.db.base import Base

    # testcontainers возвращает psycopg2-URL; заменяем драйвер на asyncpg
    sync_url: str = pg_container.get_connection_url()
    async_url = sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg", 1)

    engine = create_async_engine(async_url, echo=False, pool_size=5)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture()
def pg_session_factory(pg_engine):
    """Фабрика сессий; каждый тест получает независимые сессии."""
    return async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture()
def pg_uow_factory(pg_session_factory):
    """Фабрика, которая возвращает новый SqlAlchemyUnitOfWork на каждый вызов.
    Каждый UoW открывает свою сессию — имитируем независимые DB-соединения.
    """
    from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

    def _factory():
        session = pg_session_factory()
        return SqlAlchemyUnitOfWork(session)

    return _factory


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

async def _seed_document(
    pg_uow_factory,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    document_id: uuid.UUID,
    review_version: int = 0,
) -> None:
    """Вставляет минимальную запись документа напрямую через SQL.

    Не используем API (чтобы не зависеть от логики вышележащих слоёв).
    Зависимости: users, projects — вставляется в правильном порядке (FK).
    """
    uow = pg_uow_factory()
    async with uow:
        await uow._session.execute(
            text("""
                INSERT INTO users (id, email, hashed_password, is_active)
                VALUES (:uid, :email, 'x', true)
                ON CONFLICT DO NOTHING
            """),
            {"uid": str(user_id), "email": f"test_{user_id}@example.com"},
        )
        await uow._session.execute(
            text("""
                INSERT INTO projects (id, name, owner_id)
                VALUES (:pid, 'test-project', :uid)
                ON CONFLICT DO NOTHING
            """),
            {"pid": str(project_id), "uid": str(user_id)},
        )
        await uow._session.execute(
            text("""
                INSERT INTO analysis_jobs (id, project_id, status)
                VALUES (:jid, :pid, 'completed')
                ON CONFLICT DO NOTHING
            """),
            {"jid": str(job_id), "pid": str(project_id)},
        )
        await uow._session.execute(
            text("""
                INSERT INTO documents
                    (id, project_id, owner_id, current_analysis_job_id,
                     status, review_version, title)
                VALUES
                    (:did, :pid, :uid, :jid,
                     'awaiting_approval', :rv, 'doc-for-test')
                ON CONFLICT DO NOTHING
            """),
            {
                "did": str(document_id),
                "pid": str(project_id),
                "uid": str(user_id),
                "jid": str(job_id),
                "rv":  review_version,
            },
        )
        await uow.commit()


async def _seed_suggestion(
    pg_uow_factory,
    *,
    suggestion_id: uuid.UUID,
    document_id: uuid.UUID,
    job_id: uuid.UUID,
) -> None:
    uow = pg_uow_factory()
    async with uow:
        await uow._session.execute(
            text("""
                INSERT INTO suggestions
                    (id, document_id, analysis_job_id, status,
                     original_text, suggested_text, position_start, position_end)
                VALUES
                    (:sid, :did, :jid, 'pending', 'old', 'new', 0, 3)
                ON CONFLICT DO NOTHING
            """),
            {
                "sid": str(suggestion_id),
                "did": str(document_id),
                "jid": str(job_id),
            },
        )
        await uow.commit()


def _ids():
    return {
        "project":  uuid.uuid4(),
        "document": uuid.uuid4(),
        "job":      uuid.uuid4(),
        "user":     uuid.uuid4(),
        "s1":       uuid.uuid4(),
        "s2":       uuid.uuid4(),
        "s3":       uuid.uuid4(),
    }


# ---------------------------------------------------------------------------
# TEST 1: Два конкурентных запроса — ровно один побеждает
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_pg_concurrent_same_version_one_wins(pg_uow_factory):
    """
    Два asyncio-таска одновременно стартуют с review_version=0.
    PostgreSQL-CAS гарантирует: только один UPDATE получает RETURNING-строку.
    Второй видит уже review_version=1 — OptimisticLockError.
    """
    from app.domain.exceptions import OptimisticLockError
    from app.domain.services.suggestion_service import SuggestionService

    ids = _ids()
    await _seed_document(pg_uow_factory, **{k: ids[k] for k in ("project", "document", "job", "user")})
    await _seed_suggestion(pg_uow_factory, suggestion_id=ids["s1"], document_id=ids["document"], job_id=ids["job"])
    await _seed_suggestion(pg_uow_factory, suggestion_id=ids["s2"], document_id=ids["document"], job_id=ids["job"])

    async def _call(accepted_id: uuid.UUID):
        uow = pg_uow_factory()
        svc = SuggestionService(uow)
        return await svc.atomic_review_save(
            project_id=ids["project"],
            document_id=ids["document"],
            user_id=ids["user"],
            review_version=0,
            accepted_ids=(accepted_id,),
            rejected_ids=(),
            finalize=False,
        )

    results = await asyncio.gather(
        _call(ids["s1"]),
        _call(ids["s2"]),
        return_exceptions=True,
    )

    successes   = [r for r in results if not isinstance(r, Exception)]
    lock_errors = [r for r in results if isinstance(r, OptimisticLockError)]

    assert len(successes)   == 1, f"Ожидался 1 успех, получено: {results}"
    assert len(lock_errors) == 1, f"Ожидался 1 OptimisticLockError, получено: {results}"


# ---------------------------------------------------------------------------
# TEST 2: Три параллельных — ровно один победитель
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_pg_three_concurrent_one_wins(pg_uow_factory):
    """3 таска с version=0 → ровно 1 успех, 2 OptimisticLockError."""
    from app.domain.exceptions import OptimisticLockError
    from app.domain.services.suggestion_service import SuggestionService

    ids = _ids()
    await _seed_document(pg_uow_factory, **{k: ids[k] for k in ("project", "document", "job", "user")})
    await _seed_suggestion(pg_uow_factory, suggestion_id=ids["s1"], document_id=ids["document"], job_id=ids["job"])

    async def _call():
        uow = pg_uow_factory()
        svc = SuggestionService(uow)
        return await svc.atomic_review_save(
            project_id=ids["project"],
            document_id=ids["document"],
            user_id=ids["user"],
            review_version=0,
            accepted_ids=(ids["s1"],),
            rejected_ids=(),
            finalize=False,
        )

    results = await asyncio.gather(_call(), _call(), _call(), return_exceptions=True)
    from app.domain.exceptions import OptimisticLockError

    successes   = [r for r in results if not isinstance(r, Exception)]
    lock_errors = [r for r in results if isinstance(r, OptimisticLockError)]

    assert len(successes)   == 1
    assert len(lock_errors) == 2


# ---------------------------------------------------------------------------
# TEST 3: Последовательные сохранения — версия растёт 0→1→2
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_pg_sequential_saves_increment(pg_uow_factory):
    """v0 → v1 → v2: каждый вызов commit-ит и возвращает incremented_version."""
    from app.domain.services.suggestion_service import SuggestionService

    ids = _ids()
    await _seed_document(pg_uow_factory, **{k: ids[k] for k in ("project", "document", "job", "user")})
    await _seed_suggestion(pg_uow_factory, suggestion_id=ids["s1"], document_id=ids["document"], job_id=ids["job"])
    await _seed_suggestion(pg_uow_factory, suggestion_id=ids["s2"], document_id=ids["document"], job_id=ids["job"])
    await _seed_suggestion(pg_uow_factory, suggestion_id=ids["s3"], document_id=ids["document"], job_id=ids["job"])

    uow1 = pg_uow_factory()
    r1 = await SuggestionService(uow1).atomic_review_save(
        project_id=ids["project"],
        document_id=ids["document"],
        user_id=ids["user"],
        review_version=0,
        accepted_ids=(ids["s1"],),
        rejected_ids=(),
        finalize=False,
    )
    assert r1.new_review_version == 1

    uow2 = pg_uow_factory()
    r2 = await SuggestionService(uow2).atomic_review_save(
        project_id=ids["project"],
        document_id=ids["document"],
        user_id=ids["user"],
        review_version=1,
        accepted_ids=(ids["s2"],),
        rejected_ids=(),
        finalize=False,
    )
    assert r2.new_review_version == 2


# ---------------------------------------------------------------------------
# TEST 4: Победитель поднял version — опоздавший с version=0 горит,
#          позвонивший с version=1 проходит
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_pg_stale_version_after_win(pg_uow_factory):
    """
    1. first_caller: version=0 → успех, база становится review_version=1.
    2. late_caller:  version=0 → OptimisticLockError (устаревшая версия).
    3. good_caller:  version=1 → успех, review_version=2.
    """
    from app.domain.exceptions import OptimisticLockError
    from app.domain.services.suggestion_service import SuggestionService

    ids = _ids()
    await _seed_document(pg_uow_factory, **{k: ids[k] for k in ("project", "document", "job", "user")})
    await _seed_suggestion(pg_uow_factory, suggestion_id=ids["s1"], document_id=ids["document"], job_id=ids["job"])
    await _seed_suggestion(pg_uow_factory, suggestion_id=ids["s2"], document_id=ids["document"], job_id=ids["job"])
    await _seed_suggestion(pg_uow_factory, suggestion_id=ids["s3"], document_id=ids["document"], job_id=ids["job"])

    # 1. first_caller wins
    r1 = await SuggestionService(pg_uow_factory()).atomic_review_save(
        project_id=ids["project"], document_id=ids["document"],
        user_id=ids["user"], review_version=0,
        accepted_ids=(ids["s1"],), rejected_ids=(), finalize=False,
    )
    assert r1.new_review_version == 1

    # 2. late_caller — всё ещё держит version=0
    with pytest.raises(OptimisticLockError):
        await SuggestionService(pg_uow_factory()).atomic_review_save(
            project_id=ids["project"], document_id=ids["document"],
            user_id=ids["user"], review_version=0,
            accepted_ids=(ids["s2"],), rejected_ids=(), finalize=False,
        )

    # 3. good_caller — знает актуальную версию
    r3 = await SuggestionService(pg_uow_factory()).atomic_review_save(
        project_id=ids["project"], document_id=ids["document"],
        user_id=ids["user"], review_version=1,
        accepted_ids=(ids["s3"],), rejected_ids=(), finalize=False,
    )
    assert r3.new_review_version == 2
