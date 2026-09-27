"""
SSE-роутер — real-time обновления статусов документов без WebSocket
и без перезагрузки страницы.

GET /api/v1/events/documents
  Фронт подключается как EventSource и получает события:

  - document_status_changed   {document_id, status, pending_suggestions}
  - attention_count_changed   {count}              — кол-во AWAITING_APPROVAL
  - dashboard_stats_changed   {total, awaiting, ready, relevance_percent}
  - ping                      {}                   — keepalive каждые 25 с

Параметр ?document_ids=uuid1,uuid2,...  — фильтр: слать только события
по конкретным документам (опционально, макс 50 ID).

Архитектура (R-1 fix):
  По умолчанию используется RedisPubSubBroker — каждый инстанс FastAPI
  подписывается на общий Redis-канал, поэтому события от воркеров
  доставляются ВСЕМ подключённым клиентам независимо от того, к какому
  инстансу они подключены.

  Если переменная среды REDIS_SSE_PUBSUB_CHANNEL не задана или Redis
  недоступен при старте, автоматически активируется InMemorySSEBroker
  (старое поведение — допустимо для single-instance деплоя).

FIX-5: endpoint теперь возвращает HTTP 422 если передано > 50 document_ids,
  вместо молчаливого усечения. Клиент получает явную ошибку.
FIX-4: get_redis_client() использует Redis.from_url() — конструктор без
  сетевого вызова (соединение ленивое). Блокировок event loop нет — no-op.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.infrastructure.db.models.user import User

logger = logging.getLogger("syncscribe.api.sse")

router = APIRouter(prefix="/events", tags=["sse"])

PING_INTERVAL = 25
DOCUMENT_IDS_MAX = 50


@dataclass
class SSEEvent:
    """Одно SSE-событие, отправляемое клиенту."""
    event: str
    data: dict = field(default_factory=dict)
    document_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None

    def to_sse_bytes(self) -> bytes:
        payload = {"event": self.event, **self.data}
        return (
            f"event: {self.event}\n"
            f"data: {json.dumps(payload)}\n\n"
        ).encode()

    def to_json(self) -> str:
        return json.dumps({
            "event": self.event,
            "data": self.data,
            "document_id": str(self.document_id) if self.document_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
        })

    @staticmethod
    def from_json(raw: str) -> "SSEEvent":
        d = json.loads(raw)
        return SSEEvent(
            event=d["event"],
            data=d.get("data", {}),
            document_id=uuid.UUID(d["document_id"]) if d.get("document_id") else None,
            user_id=uuid.UUID(d["user_id"]) if d.get("user_id") else None,
        )


class ISSEBroker(ABC):
    @abstractmethod
    async def publish(self, event: SSEEvent) -> None: ...

    @abstractmethod
    async def subscribe(
        self,
        user_id: uuid.UUID,
        document_ids: frozenset[uuid.UUID],
    ) -> tuple[str, asyncio.Queue]: ...

    @abstractmethod
    def unsubscribe(self, sub_id: str) -> None: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...


class InMemorySSEBroker(ISSEBroker):
    """In-memory pub/sub — используется только при single-instance деплое."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[SSEEvent | None]] = {}
        self._meta: dict[str, tuple[uuid.UUID, frozenset[uuid.UUID]]] = {}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        for q in self._queues.values():
            await q.put(None)
        self._queues.clear()
        self._meta.clear()

    async def subscribe(
        self,
        user_id: uuid.UUID,
        document_ids: frozenset[uuid.UUID],
    ) -> tuple[str, asyncio.Queue]:
        sub_id = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._queues[sub_id] = q
        self._meta[sub_id] = (user_id, document_ids)
        return sub_id, q

    def unsubscribe(self, sub_id: str) -> None:
        self._queues.pop(sub_id, None)
        self._meta.pop(sub_id, None)

    async def publish(self, event: SSEEvent) -> None:
        for sub_id, q in list(self._queues.items()):
            meta = self._meta.get(sub_id)
            if meta is None:
                continue
            sub_user_id, filter_ids = meta
            if event.user_id is not None and event.user_id != sub_user_id:
                continue
            if filter_ids and event.document_id is not None:
                if event.document_id not in filter_ids:
                    continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("SSE queue full for %s, dropping", sub_id[:8])


class RedisPubSubBroker(ISSEBroker):
    """Redis Pub/Sub брокер — поддерживает мульти-инстанс деплой."""

    def __init__(self, redis_url: str, channel: str = "syncscribe:sse") -> None:
        self._redis_url = redis_url
        self._channel = channel
        self._queues: dict[str, asyncio.Queue] = {}
        self._meta: dict[str, tuple[uuid.UUID, frozenset[uuid.UUID]]] = {}
        self._reader_task: asyncio.Task | None = None
        self._redis = None
        self._pubsub = None

    async def start(self) -> None:
        import redis.asyncio as aioredis
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        self._pubsub = self._redis.pubsub()
        await self._pubsub.subscribe(self._channel)
        self._reader_task = asyncio.create_task(self._reader_loop())
        logger.info("RedisPubSubBroker started (channel=%s)", self._channel)

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            await self._pubsub.unsubscribe(self._channel)
            await self._pubsub.close()
        if self._redis:
            await self._redis.aclose()
        for q in self._queues.values():
            await q.put(None)
        self._queues.clear()
        self._meta.clear()

    async def _reader_loop(self) -> None:
        try:
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    event = SSEEvent.from_json(message["data"])
                except Exception as exc:
                    logger.warning("Невалидное SSE-сообщение из Redis: %s", exc)
                    continue
                await self._fan_out(event)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("RedisPubSubBroker reader loop crashed: %s", exc)

    async def _fan_out(self, event: SSEEvent) -> None:
        for sub_id, q in list(self._queues.items()):
            meta = self._meta.get(sub_id)
            if meta is None:
                continue
            sub_user_id, filter_ids = meta
            if event.user_id is not None and event.user_id != sub_user_id:
                continue
            if filter_ids and event.document_id is not None:
                if event.document_id not in filter_ids:
                    continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("SSE queue full for %s, dropping", sub_id[:8])

    async def subscribe(
        self,
        user_id: uuid.UUID,
        document_ids: frozenset[uuid.UUID],
    ) -> tuple[str, asyncio.Queue]:
        sub_id = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._queues[sub_id] = q
        self._meta[sub_id] = (user_id, document_ids)
        return sub_id, q

    def unsubscribe(self, sub_id: str) -> None:
        self._queues.pop(sub_id, None)
        self._meta.pop(sub_id, None)

    async def publish(self, event: SSEEvent) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.publish(self._channel, event.to_json())
        except Exception as exc:
            logger.warning("Redis publish failed: %s", exc)


_broker: ISSEBroker | None = None


async def init_sse_broker(redis_url: str | None = None, channel: str = "syncscribe:sse") -> ISSEBroker:
    """Инициализировать брокер при старте приложения (вызывать из lifespan)."""
    global _broker
    if redis_url:
        try:
            broker = RedisPubSubBroker(redis_url=redis_url, channel=channel)
            await broker.start()
            _broker = broker
            logger.info("SSE: использует Redis Pub/Sub (%s)", channel)
        except Exception as exc:
            logger.warning(
                "SSE: Redis недоступен (%s), fallback на in-memory брокер", exc
            )
            _broker = InMemorySSEBroker()
    else:
        _broker = InMemorySSEBroker()
        logger.info("SSE: использует in-memory брокер (single-instance only)")
    return _broker


async def shutdown_sse_broker() -> None:
    global _broker
    if _broker is not None:
        await _broker.stop()
        _broker = None


def get_sse_broker() -> ISSEBroker:
    global _broker
    if _broker is None:
        _broker = InMemorySSEBroker()
    return _broker


async def _event_stream(
    request: Request,
    user_id: uuid.UUID,
    document_ids: frozenset[uuid.UUID],
    broker: ISSEBroker,
) -> AsyncIterator[bytes]:
    sub_id, q = await broker.subscribe(user_id, document_ids)
    try:
        while True:
            try:
                event: SSEEvent | None = await asyncio.wait_for(
                    q.get(), timeout=PING_INTERVAL
                )
            except asyncio.TimeoutError:
                yield b"event: ping\ndata: {}\n\n"
                continue

            if event is None:
                break

            yield event.to_sse_bytes()

            if await request.is_disconnected():
                break
    finally:
        broker.unsubscribe(sub_id)


@router.get("/documents", summary="SSE: real-time статусы документов")
async def document_events(
    request: Request,
    document_ids: str | None = Query(
        default=None,
        description=f"Опциональный фильтр: UUID через запятую (макс {DOCUMENT_IDS_MAX}).",
    ),
    current_user: User = Depends(get_current_user),
    broker: ISSEBroker = Depends(get_sse_broker),
) -> StreamingResponse:
    filter_ids: frozenset[uuid.UUID] = frozenset()
    if document_ids:
        raw_ids = [s.strip() for s in document_ids.split(",") if s.strip()]

        if len(raw_ids) > DOCUMENT_IDS_MAX:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Параметр document_ids содержит {len(raw_ids)} значений. "
                    f"Максимально допустимо: {DOCUMENT_IDS_MAX}."
                ),
            )

        parsed: list[uuid.UUID] = []
        for raw in raw_ids:
            try:
                parsed.append(uuid.UUID(raw))
            except ValueError:
                pass
        filter_ids = frozenset(parsed)

    return StreamingResponse(
        _event_stream(request, current_user.id, filter_ids, broker),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
