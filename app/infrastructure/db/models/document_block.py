"""
P0-3: Блочная модель документа.

Вместо хранения всего текста как одное поле documents.raw_text,
документ разбивается на блоки (заголовки, абзацы, списки).
Правки привязываются к конкретному блоку, что даёт позиционирование
правки в UI без разбора строк.

Рефактор включает:
  1. Новая таблица document_blocks (этот файл)
  2. Миграция Alembic (alembic/versions/xxxx_p0_3_document_blocks.py)
  3. Рефактор парсеров: вернуть List[DocumentBlock] вместо str
  4. Обновление Suggestion.block_id FK (op.) → document_blocks.id
  5. Адаптация LLM-промпта под блоки
Требует отдельного PR и Code Review до мержа в main.
"""
import uuid
import enum

from sqlalchemy import ForeignKey, Integer, String, Text, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base


class BlockType(str, enum.Enum):
    """P0-3: тип блока документа."""
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    CODE = "code"
    TABLE = "table"
    OTHER = "other"


class DocumentBlock(Base):
    """P0-3: один блок документа.

    TODO до мержа:
    - добавить миграцию Alembic (CREATE TABLE document_blocks)
    - добавить FK block_id в таблице suggestions (nullable, opaque)
    - обновить парсеры: вернуть List[DocumentBlock]
    """
    __tablename__ = "document_blocks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[BlockType] = mapped_column(
        SAEnum(BlockType, name="block_type_enum"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    heading_level: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-6 для HEADING
    raw_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)  # исходник
