"""Событие воркера доходит до подписчика API через канал Redis (fakeredis)."""

from __future__ import annotations

import asyncio
import uuid

import fakeredis.aioredis
import pytest

from app.domain.events import DocumentStatusChanged
from app.domain.value_objects import DocumentStatusVO
from app.infrastructure.events import publishers, sse_broker
from app.infrastructure.events.publishers import RedisEventPublisher
from app.infrastructure.events.sse_broker import RedisPubSubBroker

pytestmark = pytest.mark.asyncio


async def test_worker_publication_reaches_api_subscriber(monkeypatch: pytest.MonkeyPatch) -> None:
    server = fakeredis.FakeServer()

    def fake_from_url(url: str, **kwargs):
        return fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

    monkeypatch.setattr("redis.asyncio.from_url", fake_from_url)
    monkeypatch.setattr(publishers.Redis, "from_url", staticmethod(fake_from_url))

    broker = RedisPubSubBroker("redis://unused", channel="test:sse")
    await broker.start()
    owner, document_id = uuid.uuid4(), uuid.uuid4()
    try:
        _, queue = await broker.subscribe(owner, frozenset())
        _, foreign_queue = await broker.subscribe(uuid.uuid4(), frozenset())
        await asyncio.sleep(0.05)

        await RedisEventPublisher("redis://unused", "test:sse").publish(
            DocumentStatusChanged(document_id, uuid.uuid4(), owner, DocumentStatusVO.READY, None)
        )

        event = await asyncio.wait_for(queue.get(), timeout=2)
        assert event is not None
        assert event.event == "document_status_changed"
        assert event.document_id == document_id and event.user_id == owner
        assert event.data["status"] == "ready"
        assert foreign_queue.empty()
    finally:
        await broker.stop()
    assert queue.get_nowait() is None


async def test_init_falls_back_to_in_memory_when_redis_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_start(self) -> None:
        raise ConnectionError("no redis")

    monkeypatch.setattr(RedisPubSubBroker, "start", failing_start)
    broker = await sse_broker.init_sse_broker("redis://unused", "test:sse")
    try:
        assert isinstance(broker, sse_broker.InMemorySSEBroker)
        assert sse_broker.get_sse_broker() is broker
    finally:
        await sse_broker.shutdown_sse_broker()
