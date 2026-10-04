"""Outbox публикует смену статуса документа только после commit и адресует её владельцу."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.domain.events import DocumentStatusChanged, DomainEvent
from app.domain.services.document_events import DocumentEventOutbox
from app.domain.value_objects import DocumentStatusVO

pytestmark = pytest.mark.asyncio


@dataclass
class RecordingPublisher:
    events: list[DomainEvent]
    fail: bool = False

    async def publish(self, event: DomainEvent) -> None:
        if self.fail:
            raise ConnectionError("redis down")
        self.events.append(event)


class _Projects:
    def __init__(self, owners: dict[uuid.UUID, uuid.UUID]) -> None:
        self._owners = owners
        self.calls = 0

    async def get_by_id(self, project_id: uuid.UUID):
        self.calls += 1
        owner = self._owners.get(project_id)
        return None if owner is None else SimpleNamespace(id=project_id, owner_id=owner)


def _document(project_id: uuid.UUID, status: DocumentStatusVO):
    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        status=status,
        current_analysis_job_id=None,
        review_version=0,
    )


async def test_flush_publishes_recorded_changes_with_owner() -> None:
    project_id, owner_id = uuid.uuid4(), uuid.uuid4()
    projects = _Projects({project_id: owner_id})
    publisher = RecordingPublisher([])
    outbox = DocumentEventOutbox(publisher)
    first = _document(project_id, DocumentStatusVO.IN_PROGRESS)
    second = _document(project_id, DocumentStatusVO.READY)
    outbox.record(first)
    outbox.record(second)

    await outbox.flush(SimpleNamespace(projects=projects))

    assert publisher.events == [
        DocumentStatusChanged(first.id, project_id, owner_id, DocumentStatusVO.IN_PROGRESS, None),
        DocumentStatusChanged(second.id, project_id, owner_id, DocumentStatusVO.READY, None),
    ]
    assert projects.calls == 1
    await outbox.flush(SimpleNamespace(projects=projects))
    assert len(publisher.events) == 2


async def test_outbox_without_publisher_records_nothing() -> None:
    outbox = DocumentEventOutbox(None)
    outbox.record(_document(uuid.uuid4(), DocumentStatusVO.DRAFT))
    # uow без репозитория проектов: при выключенных событиях он не нужен
    await outbox.flush(SimpleNamespace())


async def test_publisher_failure_and_unknown_project_do_not_break_flow() -> None:
    project_id = uuid.uuid4()
    publisher = RecordingPublisher([], fail=True)
    outbox = DocumentEventOutbox(publisher)
    outbox.record(_document(project_id, DocumentStatusVO.DRAFT))
    outbox.record(_document(uuid.uuid4(), DocumentStatusVO.DRAFT))

    await outbox.flush(SimpleNamespace(projects=_Projects({project_id: uuid.uuid4()})))

    assert publisher.events == []


async def test_discard_forgets_recorded_changes() -> None:
    publisher = RecordingPublisher([])
    outbox = DocumentEventOutbox(publisher)
    outbox.record(_document(uuid.uuid4(), DocumentStatusVO.DRAFT))
    outbox.discard()
    await outbox.flush(SimpleNamespace(projects=_Projects({})))
    assert publisher.events == []
