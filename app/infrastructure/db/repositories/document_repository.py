from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, exists, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import IDocumentRepository
from app.domain.value_objects import DocumentStatusVO, KeysetPage, PaginationParams

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.enums import DocumentFormat

_ANALYZABLE_STATUSES: frozenset[DocumentStatusVO] = frozenset(
    {
        DocumentStatusVO.DRAFT,
        DocumentStatusVO.AWAITING_APPROVAL,
    }
)

_SORT_COLUMNS = frozenset({"created_at", "updated_at", "name"})


def _status_to_orm(vo: DocumentStatusVO):
    from app.infrastructure.db.models.enums import DocumentStatus

    return DocumentStatus(vo.value)


class DocumentRepository(IDocumentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        id: uuid.UUID,
        project_id: uuid.UUID,
        name: str,
        format: DocumentFormat,
        storage_key: str,
        size_bytes: int,
    ) -> Document:
        from app.infrastructure.db.models.document import Document as M
        from app.infrastructure.db.models.enums import DocumentStatus

        doc = M(
            id=id,
            project_id=project_id,
            name=name,
            format=format,
            storage_key=storage_key,
            size_bytes=size_bytes,
            uploaded_at=datetime.now(UTC),
            status=DocumentStatus.DRAFT,
        )
        self._session.add(doc)
        await self._session.flush()
        return doc

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        from app.infrastructure.db.models.document import Document as M

        return await self._session.get(M, document_id)

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        pagination: KeysetPage | PaginationParams,
        *,
        status: DocumentStatusVO | None = None,
    ) -> list[Document]:
        from app.infrastructure.db.models.document import Document as M

        q = (
            select(M)
            .where(M.project_id == project_id)
            .order_by(M.created_at.desc(), M.id.desc())
            .limit(pagination.limit)
        )

        if status is not None:
            q = q.where(M.status == _status_to_orm(status))

        if isinstance(pagination, KeysetPage) and pagination.has_cursor:
            q = q.where(
                tuple_(M.created_at, M.id)
                < tuple_(pagination.before_created_at, pagination.before_id)
            )
        elif isinstance(pagination, PaginationParams):
            q = q.offset(pagination.offset)

        result = await self._session.execute(q)
        return list(result.scalars().all())

    async def count_for_project(
        self,
        project_id: uuid.UUID,
        *,
        status: DocumentStatusVO | None = None,
    ) -> int:
        from app.infrastructure.db.models.document import Document as M

        q = select(func.count()).select_from(M).where(M.project_id == project_id)
        if status is not None:
            q = q.where(M.status == _status_to_orm(status))
        result = await self._session.execute(q)
        return result.scalar_one()

    async def list_analyzable_for_project(
        self,
        project_id: uuid.UUID,
    ) -> list[Document]:
        from app.infrastructure.db.models.document import Document as M

        analyzable_orm = tuple(_status_to_orm(s) for s in _ANALYZABLE_STATUSES)
        result = await self._session.execute(
            select(M).where(
                M.project_id == project_id,
                M.status.in_(analyzable_orm),
            )
        )
        return list(result.scalars().all())

    async def get_stats_for_project(
        self,
        project_id: uuid.UUID,
    ) -> dict[str, int]:
        from app.infrastructure.db.models.document import Document as M

        stat_cols = [
            func.count().filter(M.status == _status_to_orm(vo)).label(vo.value)
            for vo in DocumentStatusVO
        ]
        stat_cols.append(func.count().label("total"))

        rows = await self._session.execute(select(*stat_cols).where(M.project_id == project_id))
        row = rows.one()
        result: dict[str, int] = {vo.value: getattr(row, vo.value) for vo in DocumentStatusVO}
        result["total"] = row.total
        return result

    async def list_all_for_user(
        self,
        user_id: uuid.UUID,
        limit: int,
        offset: int,
        status: DocumentStatusVO | None = None,
        outdated: bool = False,
        search: str | None = None,
        sort_by: str = "updated_at",
        sort_dir: str = "desc",
    ) -> tuple[list[dict[str, Any]], int]:
        """OPT-3: CTE + window COUNT() OVER () — 1 round-trip вместо 2.

        M-1: маппинг результата выполняется по явному label "doc",
        а не по хрупкому строковому ключу "Document" (имя ORM-класса
        может измениться в новых версиях SA).
        """
        from app.infrastructure.db.models.document import Document as M
        from app.infrastructure.db.models.enums import SuggestionStatus
        from app.infrastructure.db.models.project import Project as P
        from app.infrastructure.db.models.suggestion import Suggestion as S

        if sort_by not in _SORT_COLUMNS:
            sort_by = "updated_at"

        suggestions_total = func.count(S.id).label("suggestions_total")
        suggestions_pending = (
            func.count(S.id)
            .filter(S.status == SuggestionStatus.PENDING)
            .label("suggestions_pending")
        )
        suggestions_accepted = (
            func.count(S.id)
            .filter(S.status == SuggestionStatus.ACCEPTED)
            .label("suggestions_accepted")
        )
        suggestions_rejected = (
            func.count(S.id)
            .filter(S.status == SuggestionStatus.REJECTED)
            .label("suggestions_rejected")
        )

        base_q = (
            select(
                M.id.label("doc_id"),
                M.label("doc"),
                P.name.label("project_name"),
                suggestions_total,
                suggestions_pending,
                suggestions_accepted,
                suggestions_rejected,
            )
            .join(P, M.project_id == P.id)
            .outerjoin(S, S.document_id == M.id)
            .where(P.owner_id == user_id)
            .group_by(M.id, P.name)
        )

        if status is not None:
            base_q = base_q.where(M.status == _status_to_orm(status))

        if outdated:
            pending_exists = exists(
                select(S.id).where(
                    S.document_id == M.id,
                    S.status == SuggestionStatus.PENDING,
                )
            )
            base_q = base_q.where(pending_exists)

        if search:
            safe_search = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            base_q = base_q.where(M.name.ilike(f"%{safe_search}%", escape="\\"))

        cte = base_q.cte("docs_cte")
        paged_q = (
            select(
                cte,
                func.count().over().label("_total"),
            )
            .order_by(
                getattr(cte.c, sort_by).asc()
                if sort_dir == "asc"
                else getattr(cte.c, sort_by).desc(),
                cte.c.doc_id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )

        result = await self._session.execute(paged_q)
        rows = result.all()

        if not rows:
            return [], 0

        total: int = rows[0]._mapping["_total"]
        items: list[dict[str, Any]] = [
            {
                "document": row._mapping["doc"],
                "project_name": row._mapping["project_name"],
                "suggestions_total": row._mapping["suggestions_total"],
                "suggestions_pending": row._mapping["suggestions_pending"],
                "suggestions_accepted": row._mapping["suggestions_accepted"],
                "suggestions_rejected": row._mapping["suggestions_rejected"],
            }
            for row in rows
        ]
        return items, total

    async def update_status(
        self,
        document: Document,
        status: DocumentStatusVO,
    ) -> Document:
        document.status = _status_to_orm(status)
        await self._session.flush()
        return document

    async def set_current_job(self, document: Document, job_id: uuid.UUID | None) -> Document:
        document.current_analysis_job_id = job_id
        await self._session.flush()
        return document

    async def update_exported_key(
        self,
        document: Document,
        export_key: str,
    ) -> None:
        document.exported_storage_key = export_key
        await self._session.flush()

    async def compare_and_increment_review_version(
        self,
        document_id: uuid.UUID,
        expected_version: int,
    ) -> Document | None:
        from app.infrastructure.db.models.document import Document as M

        stmt = (
            update(M)
            .where(
                M.id == document_id,
                M.review_version == expected_version,
            )
            .values(review_version=M.review_version + 1)
            .returning(M)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()
        if updated is None:
            return None
        await self._session.flush()
        return updated

    async def delete_document_scoped_sources(
        self,
        document_id: uuid.UUID,
    ) -> int:
        """Удалить источники scope=DOCUMENT, привязанные к данному документу.

        Выполняется одним DELETE с подзапросом:

            DELETE FROM sources
             WHERE scope = 'document'
               AND id IN (
                   SELECT source_id FROM document_sources
                    WHERE document_id = :document_id
               )

        Источники scope=PROJECT намеренно не трогаются — они принадлежат
        проекту и переживают удаление документа.

        Возвращает количество удалённых строк (для логирования/отладки).
        Должен вызываться внутри той же транзакции, что и delete / delete_by_id,
        ДО flush/commit, чтобы FK-каскад по document_sources не успел
        удалить строки раньше подзапроса.
        """
        from app.infrastructure.db.models.document_source import document_sources as DS
        from app.infrastructure.db.models.source import Source as S
        from app.infrastructure.db.models.source_scope import SourceScope

        subq = select(DS.c.source_id).where(DS.c.document_id == document_id).scalar_subquery()
        stmt = delete(S).where(
            S.scope == SourceScope.DOCUMENT,
            S.id.in_(subq),
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount

    async def delete(self, document: Document) -> None:
        await self._session.delete(document)
        await self._session.flush()

    async def delete_by_id(
        self,
        document_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> dict[str, str | None] | None:
        """M-BLOCK: удалить документ без предварительного SELECT.

        Выполняет:
            DELETE FROM documents
             WHERE id = :document_id AND project_id = :project_id
             RETURNING storage_key, original_storage_key

        Возвращает dict {'storage_key': ..., 'original_storage_key': ...}
        если строка удалена, или None если документ не найден /
        не принадлежит проекту.

        Примечание: источники scope=DOCUMENT должны быть удалены
        через delete_document_scoped_sources() ДО вызова этого метода
        (в той же транзакции), иначе FK-каскад по document_sources
        удалит join-строки раньше, чем подзапрос их прочитает.
        """
        from app.infrastructure.db.models.document import Document as M

        stmt = (
            delete(M)
            .where(
                M.id == document_id,
                M.project_id == project_id,
            )
            .returning(M.storage_key, M.original_storage_key)
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        await self._session.flush()
        return {
            "storage_key": row.storage_key,
            "original_storage_key": row.original_storage_key,
        }
