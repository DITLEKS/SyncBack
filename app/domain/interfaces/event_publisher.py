"""Порт публикации доменных событий во внешний мир (SSE, брокер сообщений)."""

from __future__ import annotations

from typing import Protocol

from app.domain.events import DomainEvent


class IEventPublisher(Protocol):
    async def publish(self, event: DomainEvent) -> None:
        """Опубликовать событие. Публикация best-effort: сбой не должен ломать сценарий."""
        ...
