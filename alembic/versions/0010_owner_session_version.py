"""owner session version (password change / disable invalidates web sessions)

Revision ID: 0010
Revises: 0009
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(
        f'ALTER TABLE "{S}".owners ADD COLUMN IF NOT EXISTS session_version '
        "INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'ALTER TABLE "{S}".owners DROP COLUMN IF EXISTS session_version')
