"""Адаптеры порта IEventPublisher: доменное событие → SSE-событие для клиентов."""

from __future__ import annotations

import logging

from redis.asyncio import Redis

from app.domain.events import DocumentStatusChanged, DomainEvent
from app.infrastructure.events.sse_broker import SSEBroker, SSEEvent

logger = logging.getLogger("syncscribe.infrastructure.events")

DOCUMENT_STATUS_CHANGED = "document_status_changed"
# Недоступный Redis не должен надолго задерживать завершение задачи воркера.
CONNECT_TIMEOUT_SECONDS = 3


def to_sse_event(event: DomainEvent) -> SSEEvent:
    if isinstance(event, DocumentStatusChanged):
        job_id = event.current_analysis_job_id
        return SSEEvent(
            event=DOCUMENT_STATUS_CHANGED,
            data={
                "document_id": str(event.document_id),
                "project_id": str(event.project_id),
                "status": event.status.value,
                "current_analysis_job_id": str(job_id) if job_id else None,
            },
            document_id=event.document_id,
            user_id=event.owner_id,
        )
    raise TypeError(f"Неизвестный тип доменного события: {type(event).__name__}")


class SSEEventPublisher:
    """Публикация через брокер процесса API: в Redis-режиме событие уйдёт во все процессы."""

    def __init__(self, broker: SSEBroker) -> None:
        self._broker = broker

    async def publish(self, event: DomainEvent) -> None:
        await self._broker.publish(to_sse_event(event))


class RedisEventPublisher:
    """Публикация напрямую в канал Redis — для воркера, у которого нет SSE-подписчиков.

    Клиент создаётся на одну публикацию: Celery-задача запускает свой event loop,
    и общий клиент между ними переиспользовать нельзя.
    """

    def __init__(self, redis_url: str, channel: str) -> None:
        self._redis_url = redis_url
        self._channel = channel

    async def publish(self, event: DomainEvent) -> None:
        client: Redis = Redis.from_url(
            self._redis_url,
            decode_responses=True,
            socket_connect_timeout=CONNECT_TIMEOUT_SECONDS,
            socket_timeout=CONNECT_TIMEOUT_SECONDS,
        )
        try:
            await client.publish(self._channel, to_sse_event(event).to_json())
        finally:
            await client.aclose()
