"""
Типы данных дашборда.

Вынесены из domain/services/dashboard_service.py в domain/interfaces/,
чтобы инфраструктурные репозитории (DashboardRepository, DocumentOpenRepository)
могли импортировать их без зависимости от сервисного слоя.
Соответствует принципу инверсии зависимостей DDD: infrastructure → domain/interfaces,
не infrastructure → domain/services.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DayActivityData:
    date: str          # ISO-8601 дата: "2026-09-15"
    analyzed: int = 0  # число завершённых analysis_jobs за этот день


@dataclass
class AttentionItem:
    id: uuid.UUID
    name: str
    project_id: uuid.UUID
    project_name: str
    pending_suggestions: int
    updated_at: datetime


@dataclass
class RecentItem:
    id: uuid.UUID
    name: str
    project_id: uuid.UUID
    project_name: str
    status: str
    last_opened_at: datetime


@dataclass
class DashboardData:
    total_documents: int
    awaiting_approval_count: int
    ready_count: int
    relevance_percent: float
    activity_last_7_days: list[DayActivityData] = field(default_factory=list)
