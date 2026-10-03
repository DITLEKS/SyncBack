"""
Document ORM model.

FIX-review-2: добавлена колонка current_analysis_job_id (nullable UUID FK → analysis_jobs.id).
    DocumentResponse и DocumentListItem объявляли это поле, но ORM-колонки не было →
    model_validate(document) всегда возвращал None даже при наличии активного job.
    Колонка nullable=True, ondelete=SET NULL — при удалении job ссылка обнуляется.
    Требует миграции: ALTER TABLE documents ADD COLUMN current_analysis_job_id UUID
    REFERENCES analysis_jobs(id) ON DELETE SET NULL;
"""

import uuid

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
    # FIX-review-2: current_analysis_job_id — денормализованная ссылка на текущий
    # (последний запущенный) job. Обнуляется автоматически при удалении job (SET NULL).
    # Заполняется в analysis_job_service при создании нового job (UPDATE documents SET
    # current_analysis_job_id = :job_id WHERE id = :doc_id).
    current_analysis_job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
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
    # FIX-review-2: relationship к текущему (активному) job без cascade.
    # foreign_keys явно указан чтобы разрешить ambiguity (два FK на analysis_jobs).
    current_analysis_job = relationship(
        "AnalysisJob",
        foreign_keys=[current_analysis_job_id],
        primaryjoin="Document.current_analysis_job_id == AnalysisJob.id",
        lazy="select",
        uselist=False,
    )
