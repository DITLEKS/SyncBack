"""
Путь в репозитории: app/infrastructure/db/models/user.py

Фикс: добавлен values_callable, чтобы SQLAlchemy отправлял в БД значение enum-члена,
а не его имя.

FIX-review-server-default: server_default оставлен как 'user' (значение, созданное
в миграции 0001). Значение 'viewer' появляется в PostgreSQL-типе user_role только
после миграции 0024. Использование 'viewer' до 0024 вызывает
InvalidTextRepresentationError при любом INSERT без явного role.
ORM-уровневый default=UserRole.VIEWER сохранён — Python всегда подставляет
значение явно, server_default срабатывает только при raw-SQL вставках.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import UserRole

if TYPE_CHECKING:
    from app.infrastructure.db.models.project import Project


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        sa.Enum(
            UserRole,
            name="user_role",
            native_enum=True,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        # server_default uses 'user' — present in user_role since migration 0001.
        # Switch to UserRole.VIEWER.value after migration 0024 has run on all envs.
        default=UserRole.VIEWER,
        server_default="user",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    projects: Mapped[list["Project"]] = relationship(
        "Project",
        back_populates="owner",
        lazy="noload",
    )
