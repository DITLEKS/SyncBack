"""Накопление событий о смене статуса документа и их публикация после коммита."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.domain.events import DocumentStatusChanged
from app.domain.interfaces.entities import DocumentProtocol
from app.domain.interfaces.event_publisher import IEventPublisher
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import DocumentStatusVO

logger = logging.getLogger("syncscribe.services.events")


@dataclass(frozen=True, slots=True)
class _PendingStatusChange:
    document_id: uuid.UUID
    project_id: uuid.UUID
    status: DocumentStatusVO
    current_analysis_job_id: uuid.UUID | None


class DocumentEventOutbox:
    """Собирает смены статуса в рамках сценария и публикует их только после commit.

    Так подписчик, получив событие, уже видит новое состояние в БД. Если publisher
    не задан, события не собираются вовсе.
    """

    def __init__(self, publisher: IEventPublisher | None) -> None:
        self._publisher = publisher
        self._pending: list[_PendingStatusChange] = []

    def record(self, document: DocumentProtocol) -> None:
        if self._publisher is None:
            return
        self._pending.append(
            _PendingStatusChange(
                document_id=document.id,
                project_id=document.project_id,
                status=DocumentStatusVO(document.status.value),
                current_analysis_job_id=document.current_analysis_job_id,
            )
        )

    async def flush(self, uow: IUnitOfWork) -> None:
        """Опубликовать накопленные события, адресуя их владельцу проекта."""
        if self._publisher is None or not self._pending:
            return
        pending, self._pending = self._pending, []
        projects = uow.projects
        owners: dict[uuid.UUID, uuid.UUID] = {}
        for change in pending:
            owner_id = owners.get(change.project_id)
            if owner_id is None:
                project = await projects.get_by_id(change.project_id)
                if project is None:
                    continue
                owner_id = owners[change.project_id] = project.owner_id
            event = DocumentStatusChanged(
                document_id=change.document_id,
                project_id=change.project_id,
                owner_id=owner_id,
                status=change.status,
                current_analysis_job_id=change.current_analysis_job_id,
            )
            try:
                await self._publisher.publish(event)
            except Exception:  # noqa: BLE001 — доставка уведомления не важнее сценария
                logger.warning(
                    "Не удалось опубликовать событие о смене статуса документа",
                    exc_info=True,
                    extra={"document_id": str(change.document_id)},
                )

    def discard(self) -> None:
        """Забыть накопленное — сценарий откатился."""
        self._pending.clear()
