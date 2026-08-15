"""
Путь в репозитории: app/infrastructure/db/models/user.py

Фикс: добавлен values_callable, чтобы SQLAlchemy отправлял в БД значение enum-члена
(например, "user"), а не его имя ("USER") — иначе asyncpg падает с
InvalidTextRepresentationError, так как тип user_role в Postgres создан со значениями
в нижнем регистре.
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
    # ИСПРАВЛЕНО (F821): импорт только для статического анализа типов.
    from app.infrastructure.db.models.project import Project


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        sa.Enum(UserRole, name="user_role", native_enum=True, values_callable=lambda enum_cls: [member.value for member in enum_cls]),
        nullable=False,
        default=UserRole.USER,
        server_default=UserRole.USER.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    projects: Mapped[list["Project"]] = relationship(back_populates="owner")
