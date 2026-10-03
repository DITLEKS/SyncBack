"""
Абстрактные интерфейсы репозиториев (порты в гексагональной архитектуре).

Правило: только чистые Python-типы и доменные value-objects.
Никаких импортов из app.infrastructure.*.

FIX-1: ISourceRepository.list_by_document_ids возвращает
  list[tuple[Source, uuid.UUID]] — кортеж (Source, document_id),
  чтобы сервисный слой мог группировать без обращения к несуществующей
  колонке Source.document_id (связь через M2M document_sources).
FIX-B1: удалён @abstractmethod delete(source_id) — метод никогда не был
  реализован в SourceRepository (единственный рабочий путь — delete_if_owned).
FIX-2 (ревю): IDocumentRepository.list_all_for_user сигнатура обновлена под
  реальный возвращаемый тип tuple[list[DocumentRow], int].
  DocumentRow — TypedDict с явным контрактом ключей.
CRIT-1: list_all_for_user расширен параметрами status/outdated/search/sort_by/sort_dir
  — приведён к реализации DocumentRepository.
CRIT-2: count_for_user и update удалены — не реализованы и не используются.
  Изменения документа идут через update_status / update_exported_key /
  compare_and_increment_review_version (объявлены явно ниже).
CRIT-3: delete принимает Document, а не UUID — приведён к реализации
  (session.delete(document)).
WARN-2: ISourceRepository.attach_to_document добавлен — M2M-вставка
  в document_sources при scope=DOCUMENT без полного replace.
R-5: ISourceRepository.list_by_project — добавлен опциональный параметр
  scope: SourceScopeVO | None = None для фильтрации по scope.
FIX-review-4: get_primary_document_id_for_source — M2M-запрос первого
  document_id для источника; используется в DELETE /sources/{id} guard.
C-2 (issue #37): ISuggestionRepository дополнен абстрактными методами
  reset_status() и bulk_reject_all() — приведён к реализации.
NEW-1: ISuggestionRepository.delete_by_analysis_job() — bulk DELETE всех
  правок job одним запросом; вызывается в create_job при повторном анализе
  (статус документа ERROR/CANCELLED) чтобы не оставлять мусор в БД.
NEW-2: ISuggestionRepository.reset_to_pending_by_job() — bulk UPDATE
  всех правок job обратно в PENDING; уже вызывался в reset_analysis(),
  но отсутствовал в интерфейсе и реализации — критический пробел.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypedDict

from app.domain.interfaces.user_repository import IUserRepository  # noqa: F401
from app.domain.value_objects import (
    AnalysisJobStatusVO,
    DocumentStatusVO,
    SourceScopeVO,
    SuggestionStatusVO,
)

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob
    from app.infrastructure.db.models.audit_log import AuditLog
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.project import Project
    from app.infrastructure.db.models.source import Source
    from app.infrastructure.db.models.suggestion import Suggestion


class DocumentRow(TypedDict):
    """Контракт строки результата list_all_for_user.

    Каждый dict в списке содержит объект Document + агрегаты
    из SQL-запроса (LEFT JOIN на projects и COUNT правок).
    Поле sources НЕ включено сюда намеренно — оно инъектируется отдельным
    батч-запросом в роутере через SourceService.list_sources_for_documents.
    """

    document: Document
    project_name: str
    suggestions_total: int
    suggestions_pending: int
    suggestions_accepted: int
    suggestions_rejected: int


class IDocumentRepository(ABC):
    @abstractmethod
    async def get_by_id(self, document_id: uuid.UUID) -> Document | None: ...

    @abstractmethod
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
    ) -> tuple[list[DocumentRow], int]:
        """CRIT-1: возвращает (список документо-строк, total_count) — один round-trip.

        Параметры фильтрации/сортировки приведены к реализации DocumentRepository.
        total_count включён в кортеж — отдельный count_for_user не нужен.
        """
        ...

    # CRIT-2: count_for_user и update удалены из интерфейса.
    # count_for_user — дублировал total из list_all_for_user.
    # update — не реализован: все мутации идут через специализированные методы ниже.

    @abstractmethod
    async def create(
        self,
        *,
        id: uuid.UUID,
        project_id: uuid.UUID,
        name: str,
        format: Any,
        storage_key: str,
        size_bytes: int,
    ) -> Document: ...

    @abstractmethod
    async def update_status(
        self,
        document_id: uuid.UUID,
        status: DocumentStatusVO,
    ) -> Document | None: ...

    @abstractmethod
    async def update_exported_key(
        self,
        document_id: uuid.UUID,
        storage_key: str,
    ) -> None: ...

    @abstractmethod
    async def compare_and_increment_review_version(
        self,
        document_id: uuid.UUID,
        expected_version: int,
    ) -> bool: ...

    @abstractmethod
    async def delete(self, document: Document) -> None:
        """CRIT-3: принимает ORM-объект Document, а не UUID.

        Реализация использует session.delete(document) — объект уже загружен
        вызывающим кодом через get_by_id, передавать id избыточно.
        """
        ...

    # --- Методы запросов, не связанные с мутациями ---

    @abstractmethod
    async def list_for_project(
        self,
        project_id: uuid.UUID,
        pagination: Any,
        *,
        status: DocumentStatusVO | None = None,
    ) -> list[Document]: ...

    @abstractmethod
    async def count_for_project(
        self,
        project_id: uuid.UUID,
        *,
        status: DocumentStatusVO | None = None,
    ) -> int: ...

    @abstractmethod
    async def list_analyzable_for_project(
        self,
        project_id: uuid.UUID,
    ) -> list[Document]: ...

    @abstractmethod
    async def get_stats_for_project(
        self,
        project_id: uuid.UUID,
    ) -> dict[str, int]: ...


class ISourceRepository(ABC):
    @abstractmethod
    async def get_by_id(self, source_id: uuid.UUID) -> Source | None: ...

    @abstractmethod
    async def get_many_by_ids(self, source_ids: list[uuid.UUID]) -> list[Source]: ...

    @abstractmethod
    async def list_by_project(
        self,
        project_id: uuid.UUID,
        limit: int,
        offset: int,
        scope: SourceScopeVO | None = None,
    ) -> list[Source]:
        """R-5: опциональная фильтрация по scope.

        scope=None (по умолчанию) — все источники проекта.
        scope=SourceScopeVO.PROJECT — только PROJECT-scope (для /sources эндпоинта).
        """
        ...

    @abstractmethod
    async def count_by_project(self, project_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def list_by_document_ids(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID],
    ) -> list[tuple[Source, uuid.UUID]]:
        """I-1 / FIX-1: батч-запрос document-scope источников через JOIN на document_sources.

        Возвращает list[(Source, document_id)] — кортежи для группировки
        в сервисном слое без обращения к несуществующей Source.document_id.
        """
        ...

    @abstractmethod
    async def get_primary_document_id_for_source(
        self,
        source_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """FIX-review-4: вернуть первый document_id из M2M-таблицы document_sources.

        Source не хранит document_id напрямую — связь через M2M.
        None означает что источник project-scope (document_sources пуст: нет связей).

        Используется в SourceService.get_primary_document_id() →
        DELETE /sources/{id} шаг 2 — перед guard активных jobив.
        """
        ...

    @abstractmethod
    async def attach_to_document(
        self,
        source_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        """WARN-2: вставить запись в document_sources без полного replace.

        Идемпотентен: повторная вставка существующей пары — no-op (INSERT OR IGNORE).
        """
        ...

    @abstractmethod
    async def create_with_id(
        self,
        source_id: uuid.UUID,
        project_id: uuid.UUID,
        name: str,
        source_type: Any,
        storage_key: str,
        scope: Any,
    ) -> Source: ...

    @abstractmethod
    async def create_url(
        self,
        project_id: uuid.UUID,
        name: str,
        url: str,
        scope: Any,
    ) -> Source: ...

    @abstractmethod
    async def replace_document_sources(
        self,
        document_id: uuid.UUID,
        sources: list[Source],
    ) -> list[Source]: ...

    # FIX-B1: delete(source_id) удалён — не реализован и не используется.
    # Единственный актуальный путь удаления — delete_if_owned (атомарная
    # проверка ownership + DELETE за один запрос, OPT-S2).

    @abstractmethod
    async def delete_if_owned(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> Source | None: ...


class ISuggestionRepository(ABC):
    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_by_id(self, suggestion_id: uuid.UUID) -> Suggestion | None: ...

    @abstractmethod
    async def list_by_analysis_job(
        self,
        analysis_job_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
        status: SuggestionStatusVO | None = None,
    ) -> list[Suggestion]: ...

    @abstractmethod
    async def list_with_total(
        self,
        analysis_job_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: SuggestionStatusVO | None = None,
    ) -> tuple[list[Suggestion], int]:
        """OPT-1: один SELECT с window-функцией вместо двух запросов.

        func.count().over() вычисляется ДО применения LIMIT/OFFSET в PostgreSQL,
        поэтому возвращает полный COUNT фильтрованной выборки.
        """
        ...

    @abstractmethod
    async def count_by_analysis_job(self, analysis_job_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def count_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> int: ...

    @abstractmethod
    async def list_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> list[Suggestion]: ...

    @abstractmethod
    async def list_by_analysis_job_and_status_page(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
        limit: int,
        offset: int = 0,
    ) -> list[Suggestion]: ...

    @abstractmethod
    async def list_ids_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> list[uuid.UUID]: ...

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    @abstractmethod
    async def bulk_create(self, suggestions: list[Suggestion]) -> list[Suggestion]: ...

    @abstractmethod
    async def update_status(
        self,
        suggestion: Suggestion,
        decision: Any,
    ) -> None:
        """Атомарный UPDATE ... WHERE status = 'pending'.

        Бросает SuggestionAlreadyDecidedError если строка не затронута.
        S-2 (иссю #37): возвращает None никогда — либо исключение, либо None имплицитно.
        """
        ...

    @abstractmethod
    async def bulk_update_status(
        self,
        decisions: Any,
    ) -> int:
        """Один UPDATE ... WHERE id IN (...) AND status = 'pending'.

        M-1 (иссю #37): scope-фильтр исправлен — decisions.document_id
        сравнивается с M.document_id, а не M.analysis_job_id.
        """
        ...

    @abstractmethod
    async def bulk_accept_all(
        self,
        analysis_job_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[Suggestion]:
        """Принять все PENDING-правки одним UPDATE, вернуть обновлённые объекты."""
        ...

    @abstractmethod
    async def bulk_reject_all(
        self,
        analysis_job_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[Suggestion]:
        """C-3 (issue #37): отклонить все PENDING-правки одним UPDATE.

        Зеркало bulk_accept_all — один UPDATE WHERE status=PENDING,
        затем SELECT обновлённых объектов.
        """
        ...

    @abstractmethod
    async def reset_status(
        self,
        suggestion: Suggestion,
    ) -> Suggestion | None:
        """C-2 (issue #37): сбросить решение правки обратно в PENDING.

        Атомарный UPDATE WHERE status != PENDING AND id = ?.
        Возвращает обновлённый объект или None если правка уже PENDING
        (сбрасывать нечего — идемпотентно со стороны репозитория).
        """
        ...

    @abstractmethod
    async def delete_by_analysis_job(
        self,
        analysis_job_id: uuid.UUID,
    ) -> int:
        """NEW-1: bulk DELETE всех правок job одним запросом.

        DELETE FROM suggestions WHERE analysis_job_id = ?.
        Возвращает количество удалённых строк (rowcount).

        Вызывается в AnalysisJobService.create_job() при повторном запуске
        анализа (статус документа ERROR или CANCELLED — FIX-ANAL-1),
        до создания новой job — чтобы старые правки предыдущего анализа
        не оставались в БД.

        Идемпотентен: если правок нет — возвращает 0, не бросает исключений.
        """
        ...

    @abstractmethod
    async def reset_to_pending_by_job(
        self,
        analysis_job_id: uuid.UUID,
    ) -> int:
        """NEW-2: bulk UPDATE всех правок job обратно в PENDING.

        UPDATE suggestions
           SET status = 'pending', decided_by = NULL, decided_at = NULL
         WHERE analysis_job_id = ? AND status != 'pending'.
        Возвращает количество затронутых строк.

        Уже вызывался в AnalysisJobService.reset_analysis(), но отсутствовал
        в интерфейсе и реализации — критический пробел.

        Идемпотентен: если все правки уже PENDING — возвращает 0.
        """
        ...


class IAnalysisJobRepository(ABC):
    @abstractmethod
    async def get_by_id(self, job_id: uuid.UUID) -> AnalysisJob | None: ...

    @abstractmethod
    async def get_by_idempotency_key(
        self, document_id: uuid.UUID, idempotency_key: str
    ) -> AnalysisJob | None: ...

    @abstractmethod
    async def get_active_by_document_id(self, document_id: uuid.UUID) -> AnalysisJob | None: ...

    @abstractmethod
    async def list_by_document(
        self, document_id: uuid.UUID, pagination: Any
    ) -> list[AnalysisJob]: ...

    @abstractmethod
    async def create_for_document(
        self,
        document: Document,
        *,
        job_id: uuid.UUID | None = None,
        status: AnalysisJobStatusVO = AnalysisJobStatusVO.PENDING,
        idempotency_key: str | None = None,
    ) -> AnalysisJob: ...

    @abstractmethod
    async def mark_dispatched(
        self, job: AnalysisJob, task_id: str
    ) -> tuple[AnalysisJob, DocumentStatusVO | None]: ...

    @abstractmethod
    async def mark_failed_queue_unavailable(
        self, job: AnalysisJob, message: str | None
    ) -> tuple[AnalysisJob, DocumentStatusVO]: ...

    @abstractmethod
    async def cancel(self, job: AnalysisJob) -> tuple[AnalysisJob, DocumentStatusVO]: ...

    @abstractmethod
    async def mark_processing_if_active(self, job_id: uuid.UUID) -> bool: ...

    @abstractmethod
    async def update_status(
        self,
        job: AnalysisJob,
        status: AnalysisJobStatusVO,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AnalysisJob: ...


class IAuditLogRepository(ABC):
    @abstractmethod
    async def create(
        self,
        *,
        document_id: uuid.UUID,
        user_id: uuid.UUID | None,
        action: str,
        suggestion_id: uuid.UUID | None = None,
        details: Any | None = None,
    ) -> AuditLog: ...

    @abstractmethod
    async def list_for_document(
        self, document_id: uuid.UUID, limit: int, offset: int
    ) -> list[AuditLog]: ...


class IProjectRepository(ABC):
    @abstractmethod
    async def get_by_id(self, project_id: uuid.UUID) -> Project | None: ...

    @abstractmethod
    async def get_for_user(self, project_id: uuid.UUID, owner_id: uuid.UUID) -> Project:
        """Проект владельца; если не найден или чужой — ProjectNotFoundError."""

    @abstractmethod
    async def create(
        self, owner_id: uuid.UUID, name: str, description: str | None = None
    ) -> Project:
        """Создать проект; сущность собирает инфраструктура."""

    @abstractmethod
    async def list_all(self, limit: int, offset: int) -> list[Project]: ...

    @abstractmethod
    async def count_all(self) -> int: ...

    @abstractmethod
    async def list_by_owner(
        self, owner_id: uuid.UUID, limit: int, offset: int
    ) -> list[Project]: ...

    @abstractmethod
    async def count_by_owner(self, owner_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def update(
        self,
        project: Project,
        name: str | None = None,
        description: str | None = None,
    ) -> Project: ...

    @abstractmethod
    async def collect_storage_keys(self, project_id: uuid.UUID) -> list[str]: ...

    @abstractmethod
    async def delete(self, project: Project) -> None: ...
