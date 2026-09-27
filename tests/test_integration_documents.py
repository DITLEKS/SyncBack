"""
Интеграционные тесты репозитория документов.

Запускаются ТОЛЬКО при наличии переменной среды:
  DATABASE_URL=postgresql+asyncpg://...

Для CI: поднять Postgres через docker-compose / GitHub Actions service.
Локально: pytest tests/test_integration_documents.py --integration

Требования:
  pip install pytest pytest-asyncio asyncpg
  Схема должна быть накатана: alembic upgrade head
"""
from __future__ import annotations

import os
import uuid

import pytest

# Пропускаем тест полностью если нет DATABASE_URL или флага --integration
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

    from app.domain.value_objects import DocumentFormatVO, DocumentStatusVO
    from app.infrastructure.db.repositories.document_repository import DocumentRepository

    engine = create_async_engine(os.environ["DATABASE_URL"], echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    doc_id = uuid.uuid4()
    project_id = uuid.uuid4()  # FK не проверяется если нет проекта — тест изолирован
    storage_key = f"test/{doc_id}.docx"

    async with session_factory() as session:
        repo = DocumentRepository(session)

        # --- Create ---
        doc = await repo.create(
            id=doc_id,
            project_id=project_id,
            name="Integration Test Document",
            format=DocumentFormatVO.DOCX,
            storage_key=storage_key,
        )
        await session.commit()

        assert doc.id == doc_id
        assert doc.name == "Integration Test Document"
        assert doc.status == DocumentStatusVO.DRAFT

        # --- Fetch ---
        fetched = await repo.get_by_id(doc_id)
        assert fetched is not None
        assert fetched.id == doc_id
        assert fetched.storage_key == storage_key

        # --- Update status ---
        updated = await repo.update_status(fetched, DocumentStatusVO.IN_PROGRESS)
        await session.commit()
        assert updated.status == DocumentStatusVO.IN_PROGRESS

        # --- Delete ---
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

    from app.domain.value_objects import DocumentFormatVO, PaginationParams
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
                format=DocumentFormatVO.DOCX,
                storage_key=f"test/{doc_id}.docx",
            )
        # Документ в другом проекте — не должен попасть в результат
        other_id = uuid.uuid4()
        await repo.create(
            id=other_id,
            project_id=other_project_id,
            name="Other project doc",
            format=DocumentFormatVO.DOCX,
            storage_key=f"test/{other_id}.docx",
        )
        await session.commit()

        docs = await repo.list_for_project(
            project_id, PaginationParams(limit=100, offset=0)
        )
        fetched_ids = {d.id for d in docs}

        assert set(ids_in_project) == fetched_ids
        assert other_id not in fetched_ids

        # Cleanup
        for d in docs:
            await repo.delete(d)
        other_doc = await repo.get_by_id(other_id)
        if other_doc:
            await repo.delete(other_doc)
        await session.commit()

    await engine.dispose()
