"""P1 batch: token prefix, wake trigger scope, question payload

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(
        f'''ALTER TABLE "{S}".members
            ADD COLUMN IF NOT EXISTS token_prefix VARCHAR(16)'''
    )
    op.execute(
        f'''ALTER TABLE "{S}".wake_config
            ADD COLUMN IF NOT EXISTS allow_from VARCHAR(12) NOT NULL DEFAULT 'any_member' '''
    )
    op.execute(
        f'''ALTER TABLE "{S}".wake_config
            ADD COLUMN IF NOT EXISTS allow_list JSONB NOT NULL DEFAULT '[]'::jsonb'''
    )
    op.execute(
        f'''ALTER TABLE "{S}".messages
            ADD COLUMN IF NOT EXISTS payload JSONB'''
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'ALTER TABLE "{S}".messages DROP COLUMN IF EXISTS payload')
    op.execute(f'ALTER TABLE "{S}".wake_config DROP COLUMN IF EXISTS allow_list')
    op.execute(f'ALTER TABLE "{S}".wake_config DROP COLUMN IF EXISTS allow_from')
    op.execute(f'ALTER TABLE "{S}".members DROP COLUMN IF EXISTS token_prefix')
