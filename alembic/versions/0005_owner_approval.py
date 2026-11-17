"""owner approval: web self-registration requires admin approval

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(
        f'''ALTER TABLE "{S}".owners
            ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ'''
    )
    # 存量用户视为已审批，避免升级后全部无法登录
    op.execute(
        f'''UPDATE "{S}".owners SET approved_at = now() WHERE approved_at IS NULL'''
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'ALTER TABLE "{S}".owners DROP COLUMN IF EXISTS approved_at')
