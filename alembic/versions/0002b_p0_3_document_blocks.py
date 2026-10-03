"""P0-3: document_blocks (перенесено в 0013)

Revision ID: 0002b
Revises: 0002a
Create Date: 2026-09-26

Ревизия оставлена пустой: таблица document_blocks создаётся в 0013 в той
форме, которую ожидают ORM-модель и миграции 0016+. Прежняя версия с
block_type varchar делала цепочку неприменимой с нуля.
"""

revision = "0002b"
down_revision = "0002a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
