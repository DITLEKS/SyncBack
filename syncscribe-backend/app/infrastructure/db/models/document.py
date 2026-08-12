"""
Путь в репозитории: app/infrastructure/db/models/document.py

Фикс: values_callable у DocumentFormat и DocumentStatus (та же причина, что у user_role).
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.document_source import document_sources
from app.infrastructure.db.models.enums import DocumentFormat, DocumentStatus

_values = lambda enum_cls: [member.value for member in enum_cls]  # noqa: E731


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    format: Mapped[DocumentFormat] = mapped_column(
        sa.Enum(DocumentFormat, name="document_format", values_callable=_values), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        sa.Enum(DocumentStatus, name="document_status", values_callable=_values),
        nullable=False,
        default=DocumentStatus.UPLOADED,
        server_default=DocumentStatus.UPLOADED.value,
    )
    current_analysis_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="SET NULL", use_alter=True, name="fk_documents_current_analysis_job"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    project: Mapped["Project"] = relationship(back_populates="documents")
    sources: Mapped[list["Source"]] = relationship(secondary=document_sources, back_populates="documents")
    analysis_jobs: Mapped[list["AnalysisJob"]] = relationship(
        back_populates="document", foreign_keys="[AnalysisJob.document_id]", cascade="all, delete-orphan"
    )
    current_analysis_job: Mapped["AnalysisJob | None"] = relationship(
        foreign_keys=[current_analysis_job_id], post_update=True, viewonly=False
    )
