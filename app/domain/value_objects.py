"""
Value-объекты доменного слоя — иммутабельные структуры данных без identity.

Эти классы не зависят от SQLAlchemy, FastAPI или любой другой инфраструктуры.
Они используются domain/services как входные/выходные типы вместо ORM-моделей.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AnalysisJobState(str, Enum):
    """Доменное перечисление состояний задачи анализа.

    Зеркалит AnalysisJobStatus из инфраструктурного слоя, но не зависит от него.
    Конвертация выполняется в адаптере (SqlAlchemy-репозитории / DI-фабрике).
    """
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class DocumentStats:
    """Агрегированная статистика документов проекта (для дашборда)."""
    total: int
    draft: int
    in_progress: int
    awaiting_approval: int
    ready: int
    failed: int
