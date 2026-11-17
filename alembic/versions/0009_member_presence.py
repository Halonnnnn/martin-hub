"""member presence (online / offline)

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(
        f'ALTER TABLE "{S}".members ADD COLUMN IF NOT EXISTS presence '
        "VARCHAR(10) NOT NULL DEFAULT 'offline'"
    )
    op.execute(
        f'ALTER TABLE "{S}".members ADD COLUMN IF NOT EXISTS presence_at TIMESTAMPTZ'
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'ALTER TABLE "{S}".members DROP COLUMN IF EXISTS presence_at')
    op.execute(f'ALTER TABLE "{S}".members DROP COLUMN IF EXISTS presence')
