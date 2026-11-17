"""pairing code history and same-owner friendships

Revision ID: 0007
Revises: 0006
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(
        f'ALTER TABLE "{S}".pairing_codes ADD COLUMN IF NOT EXISTS reclaim_name VARCHAR(100)'
    )
    op.execute(
        f'ALTER TABLE "{S}".pairing_codes ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ'
    )
    # 同 owner 的历史成员补好友关系（human 与名下 agent、agent 之间）
    op.execute(
        f'''
        INSERT INTO "{S}".friendships (member_a, member_b)
        SELECT LEAST(a.id, b.id), GREATEST(a.id, b.id)
        FROM "{S}".members a
        JOIN "{S}".members b ON a.owner_id = b.owner_id AND a.id < b.id
        WHERE a.owner_id IS NOT NULL
        ON CONFLICT DO NOTHING
        '''
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'ALTER TABLE "{S}".pairing_codes DROP COLUMN IF EXISTS revoked_at')
    op.execute(f'ALTER TABLE "{S}".pairing_codes DROP COLUMN IF EXISTS reclaim_name')
