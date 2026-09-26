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

Архитектура:
  SSEBroker — синглтон с asyncio.Queue per subscriber (in-memory fan-out).
  Публикация событий из бизнес-логики: inject SSEBroker через DI и вызывай
  await broker.publish(SSEEvent(...)).

  Масштабирование: для мульти-инстанс деплоя заменить in-memory очередь
  на Redis Pub/Sub в SSEBroker.publish() / _reader_loop() — интерфейс
  остаётся тем же.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.infrastructure.db.models.user import User

logger = logging.getLogger("syncscribe.api.sse")

router = APIRouter(prefix="/events", tags=["sse"])

# ─────────────────────────────────────────────────────────────────────────────
# Event model
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SSEEvent:
    """Одно SSE-событие, отправляемое клиенту."""
    event: str                     # тип события
    data: dict = field(default_factory=dict)
    # UUID документа-субъекта (None для глобальных событий dashboard)
    document_id: uuid.UUID | None = None
    # UUID пользователя-получателя (None = broadcast всем)
    user_id: uuid.UUID | None = None

    def to_sse_bytes(self) -> bytes:
        payload = {"event": self.event, **self.data}
        return (
            f"event: {self.event}\n"
            f"data: {json.dumps(payload)}\n\n"
        ).encode()


# ─────────────────────────────────────────────────────────────────────────────
# Broker
# ─────────────────────────────────────────────────────────────────────────────


class SSEBroker:
    """In-memory pub/sub для SSE.

    Использование:
        broker = get_sse_broker()          # синглтон через DI
        await broker.publish(SSEEvent(...))  # из любого сервиса/воркера
    """

    def __init__(self) -> None:
        # subscriber_id -> asyncio.Queue
        self._queues: dict[str, asyncio.Queue[SSEEvent | None]] = {}
        # subscriber_id -> (user_id, document_ids filter set)
        self._meta: dict[str, tuple[uuid.UUID, frozenset[uuid.UUID]]] = {}

    def _make_id(self) -> str:
        return str(uuid.uuid4())

    def subscribe(
        self,
        user_id: uuid.UUID,
        document_ids: frozenset[uuid.UUID],
    ) -> tuple[str, asyncio.Queue[SSEEvent | None]]:
        """Зарегистрировать нового подписчика. Возвращает (sub_id, queue)."""
        sub_id = self._make_id()
        q: asyncio.Queue[SSEEvent | None] = asyncio.Queue(maxsize=256)
        self._queues[sub_id] = q
        self._meta[sub_id] = (user_id, document_ids)
        logger.debug("SSE subscriber +%s (user=%s)", sub_id[:8], user_id)
        return sub_id, q

    def unsubscribe(self, sub_id: str) -> None:
        self._queues.pop(sub_id, None)
        self._meta.pop(sub_id, None)
        logger.debug("SSE subscriber -%s", sub_id[:8])

    async def publish(self, event: SSEEvent) -> None:
        """Разослать событие всем подходящим подписчикам (non-blocking put_nowait)."""
        for sub_id, q in list(self._queues.items()):
            meta = self._meta.get(sub_id)
            if meta is None:
                continue
            sub_user_id, filter_ids = meta

            # Фильтр по пользователю
            if event.user_id is not None and event.user_id != sub_user_id:
                continue

            # Фильтр по document_ids (если подписчик задал список)
            if filter_ids and event.document_id is not None:
                if event.document_id not in filter_ids:
                    continue

            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Медленный клиент — пропускаем, не блокируем остальных
                logger.warning("SSE queue full for subscriber %s, dropping event", sub_id[:8])


# Синглтон брокера — инициализируется один раз при старте приложения
_broker: SSEBroker | None = None


def init_sse_broker() -> SSEBroker:
    global _broker
    _broker = SSEBroker()
    return _broker


def get_sse_broker() -> SSEBroker:
    """FastAPI Depends / прямой вызов из сервисов."""
    if _broker is None:
        # Ленивая инициализация на случай, если lifespan ещё не отработал
        return init_sse_broker()
    return _broker


# ─────────────────────────────────────────────────────────────────────────────
# SSE stream generator
# ─────────────────────────────────────────────────────────────────────────────

PING_INTERVAL = 25  # секунд между keepalive-пингами


async def _event_stream(
    request: Request,
    user_id: uuid.UUID,
    document_ids: frozenset[uuid.UUID],
    broker: SSEBroker,
) -> AsyncIterator[bytes]:
    sub_id, q = broker.subscribe(user_id, document_ids)
    try:
        while True:
            # Ждём событие или timeout для ping
            try:
                event: SSEEvent | None = await asyncio.wait_for(
                    q.get(), timeout=PING_INTERVAL
                )
            except asyncio.TimeoutError:
                # keepalive ping
                yield b"event: ping\ndata: {}\n\n"
                continue

            if event is None:
                # Сигнал завершения от брокера
                break

            yield event.to_sse_bytes()

            # Проверяем, что клиент ещё жив
            if await request.is_disconnected():
                break
    finally:
        broker.unsubscribe(sub_id)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/documents", summary="SSE: real-time статусы документов")
async def document_events(
    request: Request,
    document_ids: str | None = Query(
        default=None,
        description=(
            "Опциональный фильтр: UUID документов через запятую (макс 50). "
            "Если не указан — получаете все события своих документов."
        ),
    ),
    current_user: User = Depends(get_current_user),
    broker: SSEBroker = Depends(get_sse_broker),
) -> StreamingResponse:
    """Server-Sent Events для real-time обновлений статусов документов.

    Фронт подключается как:
        const es = new EventSource('/api/v1/events/documents', {
            headers: { Authorization: 'Bearer <token>' }
        });
        es.addEventListener('document_status_changed', (e) => {
            const { document_id, status } = JSON.parse(e.data);
            // обновить UI без перезагрузки
        });

    Keepalive ping приходит каждые 25 секунд.
    """
    # Парсим опциональный фильтр document_ids
    filter_ids: frozenset[uuid.UUID] = frozenset()
    if document_ids:
        raw_ids = [s.strip() for s in document_ids.split(",") if s.strip()][:50]
        parsed: list[uuid.UUID] = []
        for raw in raw_ids:
            try:
                parsed.append(uuid.UUID(raw))
            except ValueError:
                pass  # невалидные UUID просто пропускаем
        filter_ids = frozenset(parsed)

    return StreamingResponse(
        _event_stream(request, current_user.id, filter_ids, broker),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # отключить nginx-буферизацию
            "Connection": "keep-alive",
        },
    )
