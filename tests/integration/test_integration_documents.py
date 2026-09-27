"""
Интеграционные тесты репозитория документов.

Запускаются ТОЛЬКО при наличии переменной среды:
  DATABASE_URL=postgresql+asyncpg://...

Для CI: поднять Postgres через docker-compose / GitHub Actions service.
Локально: pytest tests/integration/test_integration_documents.py --integration

Требования:
  pip install pytest pytest-asyncio asyncpg
  Схема должна быть накатана: alembic upgrade head
"""
from __future__ import annotations

import os
import uuid

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: тесты, требующие реальной БД"
    )


skip_without_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL не задан — пропускаем интеграционный тест",
)


@skip_without_db
@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_create_and_fetch() -> None:
    """Полный CRUD-цикл документа через реальный репозиторий и asyncpg."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.domain.value_objects import DocumentStatusVO
    from app.infrastructure.db.models.enums import DocumentFormat, DocumentStatus
    from app.infrastructure.db.repositories.document_repository import DocumentRepository

    engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    doc_id = uuid.uuid4()
    project_id = uuid.uuid4()
    storage_key = f"test/{doc_id}.docx"

    async with session_factory() as session:
        repo = DocumentRepository(session)

        doc = await repo.create(
            id=doc_id,
            project_id=project_id,
            name="Integration Test Document",
            format=DocumentFormat.DOCX,
            storage_key=storage_key,
        )
        await session.commit()

        assert doc.id == doc_id
        assert doc.name == "Integration Test Document"
        assert doc.status == DocumentStatus.DRAFT

        fetched = await repo.get_by_id(doc_id)
        assert fetched is not None
        assert fetched.id == doc_id
        assert fetched.storage_key == storage_key

        updated = await repo.update_status(fetched, DocumentStatusVO.IN_PROGRESS)
        await session.commit()
        assert updated.status == DocumentStatus.IN_PROGRESS

        await repo.delete(updated)
        await session.commit()

        gone = await repo.get_by_id(doc_id)
        assert gone is None

    await engine.dispose()


@skip_without_db
@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_list_for_project() -> None:
    """list_for_project возвращает только документы нужного проекта."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.domain.value_objects import PaginationParams
    from app.infrastructure.db.models.enums import DocumentFormat
    from app.infrastructure.db.repositories.document_repository import DocumentRepository

    engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    project_id = uuid.uuid4()
    other_project_id = uuid.uuid4()
    ids_in_project = [uuid.uuid4() for _ in range(3)]

    async with session_factory() as session:
        repo = DocumentRepository(session)

        for doc_id in ids_in_project:
            await repo.create(
                id=doc_id,
                project_id=project_id,
                name=f"Doc {doc_id}",
                format=DocumentFormat.DOCX,
                storage_key=f"test/{doc_id}.docx",
            )
        other_id = uuid.uuid4()
        await repo.create(
            id=other_id,
            project_id=other_project_id,
            name="Other project doc",
            format=DocumentFormat.DOCX,
            storage_key=f"test/{other_id}.docx",
        )
        await session.commit()

        docs = await repo.list_for_project(
            project_id, PaginationParams(limit=100, offset=0)
        )
        fetched_ids = {d.id for d in docs}

        assert set(ids_in_project) == fetched_ids
        assert other_id not in fetched_ids

        for d in docs:
            await repo.delete(d)
        other_doc = await repo.get_by_id(other_id)
        if other_doc:
            await repo.delete(other_doc)
        await session.commit()

    await engine.dispose()
