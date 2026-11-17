"""group archive

Revision ID: 0012
Revises: 0011
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(
        f'ALTER TABLE "{S}".groups ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ'
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'ALTER TABLE "{S}".groups DROP COLUMN IF EXISTS archived_at')
