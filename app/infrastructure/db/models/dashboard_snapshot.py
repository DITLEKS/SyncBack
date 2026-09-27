"""
DashboardSnapshot — ежедневный снэпшот метрик пользователя.

Записывается фоновым заданием (например, APScheduler / Celery beat)
раз в сутки, обычно в полночь UTC.

PK составной (owner_id, snapshot_date) — один снэпшот на пользователя
в сутки. ON CONFLICT DO UPDATE позволяет перезапускать джоб без дублей.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class DashboardSnapshot(Base):
    __tablename__ = "dashboard_snapshots"
    __table_args__ = (
        UniqueConstraint("owner_id", "snapshot_date", name="uq_dashboard_snapshot_owner_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)

    # — метрики, каптурируемые за день
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    awaiting_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # ready / total * 100; 0.0 если total == 0
    relevance_percent: Mapped[float] = mapped_column(Float, nullable=False)
