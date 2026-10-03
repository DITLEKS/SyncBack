"""
Бизнес-логика источников истины.

После P2:
  - create_text_source (хранил text_content в БД) удалён.
  - create_note_source: кодирует текст в UTF-8, загружает в MinIO.
  - create_url_source: сохраняет url в БД (без файла в MinIO).
  - create_file_source: без изменений.

Архитектурные правила:
  - Зависит только от IUnitOfWork (порт) и FileStorage (порт).
  - Нет импортов из app.infrastructure.* при выполнении.
  - app.core.config — допустимый non-infra импорт.

I-1: list_sources_for_documents — батч-загрузка document-scope источников.
     Репозиторий возвращает list[tuple[Source, document_id]] (FIX-1);
     сервис группирует по document_id без обращения к src.document_id.
OPT-S2: delete_source_with_guard — атомарное удаление без предварительного get_source();
        бросает SourceNotFoundError если источник не найден или принадлежит другому проекту.
        FIX-1: document_id не хранится на Source → возвращаем None (нет document-lock).
FIX-5: delete_source (deprecated) удалён — все вызовы перешли на
       delete_source_with_guard. Удаление legacy-метода устраняет путаницу.
FIX-6: create_note_source — storage.upload перенесён внутрь async with uow.
       Это гарантирует что при сбое __aenter__ cleanup storage выполнится
       в рамках единой try/except области.
       FIX-6b (review #4): то же исправление применено к create_file_source —
       upload перенесён внутрь uow, чтобы исключить MinIO orphan при сбое __aenter__.
WARN-2: create_url_source / create_note_source / create_file_source принимают
        document_id: uuid.UUID | None = None. При scope=DOCUMENT + document_id
        вызывают uow.sources.attach_to_document(source.id, document_id) — M2M-вставка.
FIX-review-4: get_primary_document_id — УДАЛЁН как публичный метод.
        Логика перенесена внутрь delete_source_with_guard для устранения TOCTOU
        между чтением document_id и проверкой active job (review #6).

review #1: _LOCKED_STATUSES не включает ERROR/CANCELLED намеренно —
        пользователь должен иметь возможность убрать сломанный источник
        до повторного запуска анализа. IN_PROGRESS и AWAITING_APPROVAL
        блокируют изменения пока идёт активная обработка/ревью.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from app.core.config import Settings, get_settings
from app.domain.exceptions import FileTooLargeError, SourceLockError, SourceNotFoundError
from app.domain.interfaces.file_storage import FileStorage
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import DocumentStatusVO, SourceScopeVO, SourceTypeVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.project import Project
    from app.infrastructure.db.models.source import Source

# Колбэк, который бросает исключение, если у документа идёт активный анализ.
_ActiveJobChecker = Callable[[uuid.UUID], Awaitable[None]]

# review #1: ERROR и CANCELLED намеренно НЕ включены в _LOCKED_STATUSES.
# Пользователь должен иметь возможность редактировать/удалять источники
# документа, завершившегося с ошибкой или отменённого, перед повторным
# запуском анализа. Блокируем только активную обработку (IN_PROGRESS)
# и этап ревью (AWAITING_APPROVAL), когда изменение источников нарушило
# бы целостность текущего job.
_LOCKED_STATUSES: frozenset[DocumentStatusVO] = frozenset(
    {
        DocumentStatusVO.IN_PROGRESS,
        DocumentStatusVO.AWAITING_APPROVAL,
    }
)


class SourceService:
    def __init__(
        self,
        uow: IUnitOfWork,
        file_storage: FileStorage,
        settings: Settings | None = None,
    ) -> None:
        self._uow = uow
        self._storage = file_storage
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # Guard
    # ------------------------------------------------------------------

    @staticmethod
    def _assert_sources_mutable(document: Document) -> None:
        if document.status in _LOCKED_STATUSES:
            raise SourceLockError(
                f"Нельзя изменить источники документа в статусе '{document.status.value}'. "
                "Дождитесь завершения анализа или переведите документ обратно в черновик."
            )

    # ------------------------------------------------------------------
    # Internal: M2M attach helper
    # ------------------------------------------------------------------

    async def _attach_if_document_scope(
        self,
        source_id: uuid.UUID,
        scope: SourceScopeVO,
        document_id: uuid.UUID | None,
    ) -> None:
        """WARN-2: если scope=DOCUMENT и document_id задан — вставить M2M-запись.

        Вызывается изнутри create_*_source после flush источника,
        в рамках того же UoW (сессия ещё открыта).
        """
        if scope is SourceScopeVO.DOCUMENT and document_id is not None:
            await self._uow.sources.attach_to_document(source_id, document_id)

    # ------------------------------------------------------------------
    # Create — note (P2: текст → MinIO как .txt)
    # ------------------------------------------------------------------

    async def create_note_source(
        self,
        project: Project,
        name: str,
        text_content: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
        document_id: uuid.UUID | None = None,
    ) -> Source:
        """Сохраняет текстовую заметку как .txt в MinIO.

        FIX-6: storage.upload перенесён внутрь async with uow — если UoW
        не открылся, cleanup не теряется. Весь процесс (upload → DB → M2M)
        обёрнут в единый try/except для атомарного rollback MinIO.

        WARN-2: при scope=DOCUMENT + document_id вставляет M2M-запись.
        """
        if len(text_content.encode()) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(f"Текст превышает лимит {self._settings.max_upload_size_mb} МБ")

        source_id = uuid.uuid4()
        storage_key = f"projects/{project.id}/sources/{source_id}/note.txt"

        try:
            async with self._uow:
                await self._storage.upload(
                    storage_key,
                    text_content.encode("utf-8"),
                    "text/plain; charset=utf-8",
                )
                source = await self._uow.sources.create_with_id(
                    source_id=source_id,
                    project_id=project.id,
                    name=name,
                    source_type=SourceTypeVO.FILE,
                    storage_key=storage_key,
                    scope=scope,
                )
                await self._attach_if_document_scope(source.id, scope, document_id)
                await self._uow.commit()
        except Exception:
            await self._storage.delete(storage_key)
            raise
        return source

    # ------------------------------------------------------------------
    # Create — url
    # ------------------------------------------------------------------

    async def create_url_source(
        self,
        project: Project,
        name: str,
        url: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
        document_id: uuid.UUID | None = None,
    ) -> Source:
        """WARN-2: при scope=DOCUMENT + document_id вставляет M2M-запись."""
        async with self._uow:
            source = await self._uow.sources.create_url(
                project_id=project.id,
                name=name,
                url=url,
                scope=scope,
            )
            await self._attach_if_document_scope(source.id, scope, document_id)
            await self._uow.commit()
        return source

    # ------------------------------------------------------------------
    # Create — file
    # ------------------------------------------------------------------

    async def create_file_source(
        self,
        project: Project,
        name: str,
        filename: str,
        content: bytes,
        content_type: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
        document_id: uuid.UUID | None = None,
    ) -> Source:
        """FIX-6b (review #4): storage.upload перенесён внутрь async with uow —
        симметрично с create_note_source (FIX-6).

        Если uow.__aenter__ упадёт, except-блок гарантированно вызовет
        storage.delete и не допустит MinIO orphan-объекта.

        WARN-2: при scope=DOCUMENT + document_id вставляет M2M-запись.
        """
        if len(content) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(f"Файл превышает лимит {self._settings.max_upload_size_mb} МБ")
        source_id = uuid.uuid4()
        storage_key = f"projects/{project.id}/sources/{source_id}/{filename}"
        try:
            async with self._uow:
                await self._storage.upload(storage_key, content, content_type)
                source = await self._uow.sources.create_with_id(
                    source_id=source_id,
                    project_id=project.id,
                    name=name,
                    source_type=SourceTypeVO.FILE,
                    storage_key=storage_key,
                    scope=scope,
                )
                await self._attach_if_document_scope(source.id, scope, document_id)
                await self._uow.commit()
        except Exception:
            await self._storage.delete(storage_key)
            raise
        return source

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_sources(
        self,
        project_id: uuid.UUID,
        limit: int,
        offset: int,
        scope: SourceScopeVO | None = None,
    ) -> tuple[list[Source], int]:
        """R-5: опциональная фильтрация по scope передаётся в репозиторий."""
        async with self._uow:
            items = await self._uow.sources.list_by_project(
                project_id, limit=limit, offset=offset, scope=scope
            )
            total = await self._uow.sources.count_by_project(project_id)
        return items, total

    async def get_source(self, project_id: uuid.UUID, source_id: uuid.UUID) -> Source:
        """Получить источник. Бросает SourceNotFoundError если не найден."""
        async with self._uow:
            source = await self._uow.sources.get_by_id(source_id)
        if source is None or source.project_id != project_id:
            raise SourceNotFoundError(f"Источник {source_id} не найден в проекте {project_id}")
        return source

    async def get_sources_for_project(
        self, project_id: uuid.UUID, source_ids: list[uuid.UUID]
    ) -> list[Source]:
        async with self._uow:
            sources = await self._uow.sources.get_many_by_ids(source_ids)

        found_ids = {s.id for s in sources}
        missing = set(source_ids) - found_ids
        if missing:
            raise SourceNotFoundError(f"Источники не найдены: {missing}")

        foreign = [s.id for s in sources if s.project_id != project_id]
        if foreign:
            raise SourceNotFoundError(f"Источники не принадлежат проекту {project_id}: {foreign}")
        return sources

    async def list_sources_for_documents(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list[Source]]:
        """I-1: батч-загрузка document-scope источников для списка документов.

        FIX-1: репозиторий возвращает list[tuple[Source, document_id]];
        группируем по document_id здесь, без обращения к src.document_id
        (колонки нет — связь через M2M document_sources).

        Возвращает {document_id: [Source, ...]} для маппинга в DocumentListItem.sources.
        """
        if not document_ids:
            return {}
        async with self._uow:
            pairs = await self._uow.sources.list_by_document_ids(project_id, document_ids)
        result: dict[uuid.UUID, list[Source]] = defaultdict(list)
        for source, doc_id in pairs:
            result[doc_id].append(source)
        return dict(result)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_source_with_guard(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
        active_job_checker: _ActiveJobChecker | None = None,
    ) -> None:
        """Атомарное удаление источника с guard-проверкой активного job.

        review #6 (TOCTOU fix): чтение document_id из M2M и проверка
        active job объединены в один uow-блок — устраняет race condition
        между get_primary_document_id и guard из отдельных uow-сессий.

        Бросает SourceNotFoundError если источник не найден или
        принадлежит другому проекту.
        """
        async with self._uow:
            source = await self._uow.sources.get_by_id(source_id)
            if source is None or source.project_id != project_id:
                raise SourceNotFoundError(f"Источник {source_id} не найден в проекте {project_id}")

            # review #6: получаем document_id и проверяем active job
            # в рамках той же транзакции — нет TOCTOU.
            document_id = await self._uow.sources.get_primary_document_id_for_source(source_id)
            if document_id is not None and active_job_checker is not None:
                await active_job_checker(document_id)

            if source.storage_key:
                await self._storage.delete(source.storage_key)
            await self._uow.sources.delete(source_id)
            await self._uow.commit()
