import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.infrastructure.db.base import Base
from app.infrastructure.db.models.enums import DocumentFormat, DocumentStatus


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_documents_size_bytes_non_negative"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(512), nullable=False)
    format = Column(
        Enum(
            DocumentFormat,
            name="document_format",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    storage_key = Column(String(1024), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    # C-2: uploaded_at актуально — колонка не дропалась ни в одной миграции;
    # используется в DashboardRepository.get_attention_documents.
    uploaded_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        Enum(
            DocumentStatus,
            name="document_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=DocumentStatus.DRAFT,
        server_default=DocumentStatus.DRAFT.value,
    )
    review_version = Column(Integer, nullable=False, default=0, server_default="0")
    # C-1: добавлено поле original_storage_key — колонка существует в БД
    # с миграции 0011. Без ORM-объявления RETURNING storage_key, original_storage_key
    # в delete_by_id падал с AttributeError, а MinIO-снапшот никогда не удалялся.
    original_storage_key = Column(String(1024), nullable=True)
    # N-2: exported_storage_key — используется в DocumentRepository.update_exported_key().
    exported_storage_key = Column(String(1024), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project = relationship("Project", back_populates="documents")
    sources = relationship("Source", secondary="document_sources", back_populates="documents")
    analysis_jobs = relationship(
        "AnalysisJob",
        back_populates="document",
        foreign_keys="AnalysisJob.document_id",
        cascade="all, delete-orphan",
    )
