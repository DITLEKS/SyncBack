"""Index on sources.document_id (пустая ревизия).

Revision ID: 0021
Revises: 0020b

У таблицы sources нет колонки document_id: связь с документами хранится в
document_sources, и у неё уже есть индекс по document_id. Ревизия оставлена
пустой, чтобы не менять цепочку для баз, где она записана.
"""

revision = "0021"
down_revision = "0020b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
