"""SSE: статусы документов в реальном времени.

GET /api/v1/events/documents — клиент подключается как EventSource и получает
``document_status_changed`` {document_id, project_id, status, current_analysis_job_id}
по документам своих проектов и ``ping`` каждые PING_INTERVAL секунд.
Параметр ``document_ids`` (UUID через запятую, не больше DOCUMENT_IDS_MAX)
ограничивает поток конкретными документами.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.infrastructure.db.models.user import User
from app.infrastructure.events.sse_broker import SSEBroker, SSEEvent, get_sse_broker

router = APIRouter(prefix="/events", tags=["sse"])

PING_INTERVAL = 25
DOCUMENT_IDS_MAX = 50


def parse_document_ids(raw: str | None) -> frozenset[uuid.UUID]:
    """Разобрать фильтр документов; невалидные UUID и лишние значения — ошибка 422."""
    if not raw:
        return frozenset()
    values = [part.strip() for part in raw.split(",") if part.strip()]
    if len(values) > DOCUMENT_IDS_MAX:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Параметр document_ids содержит {len(values)} значений. "
                f"Максимально допустимо: {DOCUMENT_IDS_MAX}."
            ),
        )
    try:
        return frozenset(uuid.UUID(value) for value in values)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Параметр document_ids должен содержать UUID через запятую",
        ) from exc


async def event_stream(
    request: Request,
    user_id: uuid.UUID,
    document_ids: frozenset[uuid.UUID],
    broker: SSEBroker,
) -> AsyncIterator[bytes]:
    sub_id, queue = await broker.subscribe(user_id, document_ids)
    try:
        while True:
            try:
                event: SSEEvent | None = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL)
            except TimeoutError:
                yield b"event: ping\ndata: {}\n\n"
                continue

            if event is None:
                break

            yield event.to_sse_bytes()

            if await request.is_disconnected():
                break
    finally:
        broker.unsubscribe(sub_id)


@router.get("/documents", summary="SSE: статусы документов в реальном времени")
async def document_events(
    request: Request,
    document_ids: str | None = Query(
        default=None,
        description=f"Опциональный фильтр: UUID через запятую (не больше {DOCUMENT_IDS_MAX}).",
    ),
    current_user: User = Depends(get_current_user),
    broker: SSEBroker = Depends(get_sse_broker),
) -> StreamingResponse:
    return StreamingResponse(
        event_stream(request, current_user.id, parse_document_ids(document_ids), broker),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
