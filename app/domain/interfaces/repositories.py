"""
Абстрактные интерфейсы репозиториев (порты в гексагональной архитектуре).

Правило: только чистые Python-типы и доменные value-objects.
Никаких импортов из app.infrastructure.*.

FIX-1: ISourceRepository.list_by_document_ids возвращает
  list[tuple[Source, uuid.UUID]] — кортеж (Source, document_id),
  чтобы сервисный слой мог группировать без обращения к несуществующей
  колонке Source.document_id (связь через M2M document_sources).
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.source import Source


class IDocumentRepository(ABC):
    @abstractmethod
    async def get_by_id(self, document_id: uuid.UUID) -> "Document | None": ...

    @abstractmethod
    async def list_all_for_user(
        self, user_id: uuid.UUID, limit: int, offset: int
    ) -> "list[Document]": ...

    @abstractmethod
    async def count_for_user(self, user_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def create(self, **kwargs) -> "Document": ...

    @abstractmethod
    async def update(self, document: "Document") -> "Document": ...

    @abstractmethod
    async def delete(self, document_id: uuid.UUID) -> None: ...


class ISourceRepository(ABC):
    @abstractmethod
    async def get_by_id(self, source_id: uuid.UUID) -> "Source | None": ...

    @abstractmethod
    async def get_many_by_ids(self, source_ids: list[uuid.UUID]) -> "list[Source]": ...

    @abstractmethod
    async def list_by_project(
        self, project_id: uuid.UUID, limit: int, offset: int
    ) -> "list[Source]": ...

    @abstractmethod
    async def count_by_project(self, project_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def list_by_document_ids(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID],
    ) -> "list[tuple[Source, uuid.UUID]]":
        """I-1 / FIX-1: батч-запрос document-scope источников через JOIN на document_sources.

        Возвращает list[(Source, document_id)] — кортежи для группировки
        в сервисном слое без обращения к несуществующей Source.document_id.
        """
        ...

    @abstractmethod
    async def create_with_id(
        self,
        source_id: uuid.UUID,
        project_id: uuid.UUID,
        name: str,
        source_type,
        storage_key: str,
        scope,
    ) -> "Source": ...

    @abstractmethod
    async def create_url(
        self,
        project_id: uuid.UUID,
        name: str,
        url: str,
        scope,
    ) -> "Source": ...

    @abstractmethod
    async def replace_document_sources(
        self,
        document_id: uuid.UUID,
        sources: "list[Source]",
    ) -> "list[Source]": ...

    @abstractmethod
    async def delete(
        self, source_id: uuid.UUID
    ) -> None: ...

    @abstractmethod
    async def delete_if_owned(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> "Source | None": ...
