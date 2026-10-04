"""События document_status_changed доходят до владельца через брокер SSE."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.infrastructure.events.sse_broker import InMemorySSEBroker, SSEEvent, get_sse_broker
from tests.contract.conftest import create_project, register_and_login, upload_document
from tests.contract.test_analysis_jobs import FakeAnalysisQueue, queue  # noqa: F401

pytestmark = pytest.mark.asyncio


async def _user_id(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    return uuid.UUID(response.json()["id"])


def _drain(queue_) -> list[SSEEvent]:
    events: list[SSEEvent] = []
    while not queue_.empty():
        events.append(queue_.get_nowait())
    return events


async def test_status_changes_are_published_to_owner_only(
    client: AsyncClient,
    queue: FakeAnalysisQueue,  # noqa: F811
) -> None:
    broker = get_sse_broker()
    assert isinstance(broker, InMemorySSEBroker)
    owner_headers = await register_and_login(client, "owner@example.com")
    stranger_headers = await register_and_login(client, "stranger@example.com")
    owner_id = await _user_id(client, owner_headers)
    stranger_id = await _user_id(client, stranger_headers)
    project_id = await create_project(client, owner_headers)
    document = await upload_document(client, owner_headers, project_id)
    owner_sub, owner_queue = await broker.subscribe(owner_id, frozenset())
    stranger_sub, stranger_queue = await broker.subscribe(stranger_id, frozenset())
    try:
        url = f"/api/v1/projects/{project_id}/documents/{document['id']}/analysis-jobs"
        job = (await client.post(url, headers=owner_headers)).json()
        assert (await client.delete(f"{url}/{job['id']}", headers=owner_headers)).status_code == 200
    finally:
        broker.unsubscribe(owner_sub)
        broker.unsubscribe(stranger_sub)

    events = _drain(owner_queue)
    assert [e.event for e in events] == ["document_status_changed"] * 2
    assert [e.data["status"] for e in events] == ["in_progress", "draft"]
    assert all(e.data["document_id"] == document["id"] for e in events)
    assert all(e.data["current_analysis_job_id"] == job["id"] for e in events)
    assert all(e.user_id == owner_id for e in events)
    assert _drain(stranger_queue) == []


async def test_sse_endpoint_rejects_too_many_document_ids(client: AsyncClient) -> None:
    headers = await register_and_login(client)
    ids = ",".join(str(uuid.uuid4()) for _ in range(51))
    response = await client.get(f"/api/v1/events/documents?document_ids={ids}", headers=headers)
    assert response.status_code == 422
    response = await client.get("/api/v1/events/documents")
    assert response.status_code == 401
