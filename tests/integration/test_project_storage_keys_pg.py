"""P0-3: collect_storage_keys собирает все ключи документа (PostgreSQL)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentFormat
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User
from app.infrastructure.db.repositories.project_repository import ProjectRepository


async def test_collect_storage_keys_includes_original_and_exported_without_duplicates(
    pg_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    suffix = uuid.uuid4().hex
    main_key = f"it/{suffix}/main.txt"
    original_key = f"it/{suffix}/original.txt"
    exported_key = f"it/{suffix}/exported.docx"

    async with pg_sessionmaker() as session:
        user = User(email=f"it-{suffix}@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(name="storage-keys", owner_id=user.id)
        session.add(project)
        await session.flush()
        session.add(
            Document(
                project_id=project.id,
                name="full.txt",
                format=DocumentFormat.TXT,
                storage_key=main_key,
                original_storage_key=original_key,
                exported_storage_key=exported_key,
                size_bytes=1,
                uploaded_at=datetime.now(UTC),
            )
        )
        session.add(
            Document(
                project_id=project.id,
                name="dup.txt",
                format=DocumentFormat.TXT,
                storage_key=main_key,
                original_storage_key=main_key,
                size_bytes=1,
                uploaded_at=datetime.now(UTC),
            )
        )
        await session.commit()
        project_id = project.id

    async with pg_sessionmaker() as session:
        keys = await ProjectRepository(session).collect_storage_keys(project_id)

    assert keys == [main_key, original_key, exported_key]
