"""Block-level document model used for stable suggestion anchors."""

import enum
import uuid

from sqlalchemy import CheckConstraint, Enum as SAEnum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class BlockType(enum.StrEnum):
    HEADING   = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    CODE      = "code"
    TABLE     = "table"
    OTHER     = "other"


class DocumentBlock(Base):
    __tablename__ = "document_blocks"
    __table_args__ = (
        # Два блока одного документа не могут занимать одну позицию.
        UniqueConstraint("document_id", "position", name="uq_document_blocks_doc_position"),
        # block_ref уникален в пределах документа (используется как anchor suggestions).
        UniqueConstraint("document_id", "block_ref", name="uq_document_blocks_doc_ref"),
        # heading_level допустим только для блоков типа heading.
        CheckConstraint(
            "block_type = 'heading' OR heading_level IS NULL",
            name="ck_document_blocks_heading_level",
        ),
        Index("ix_document_blocks_doc_position", "document_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Стабильный строковый идентификатор блока (например, "h2-3", "p-12").
    # Именно на него ссылаются suggestions.block_id.
    block_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[BlockType] = mapped_column(
        SAEnum(BlockType, name="block_type_enum", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    heading_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
