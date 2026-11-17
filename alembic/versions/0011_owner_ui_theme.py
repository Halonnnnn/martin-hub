"""owner ui theme preference

Revision ID: 0011
Revises: 0010
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(
        f'ALTER TABLE "{S}".owners ADD COLUMN IF NOT EXISTS ui_theme VARCHAR(20)'
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'ALTER TABLE "{S}".owners DROP COLUMN IF EXISTS ui_theme')
