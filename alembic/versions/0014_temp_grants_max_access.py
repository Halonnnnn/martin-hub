"""temp grants: max access window columns

Revision ID: 0014
Revises: 0013

0013 先用当时的模型建表；如果服务器已经跑过旧版 0013，表里没有
max_access_seconds / max_access_at。这里做幂等补列 + 回填，兼容旧表。
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(
        f'ALTER TABLE "{S}".temp_grants '
        "ADD COLUMN IF NOT EXISTS max_access_seconds INTEGER"
    )
    op.execute(
        f'ALTER TABLE "{S}".temp_grants '
        "ADD COLUMN IF NOT EXISTS max_access_at TIMESTAMPTZ"
    )
    # 旧授权没有最大访问窗口：回填为「访问时长 = 最大访问」，到期即不可续；
    # 新授权由创建/续期接口写入真实值。
    op.execute(
        f'UPDATE "{S}".temp_grants '
        "SET max_access_seconds = access_seconds "
        "WHERE max_access_seconds IS NULL"
    )
    op.execute(
        f'UPDATE "{S}".temp_grants '
        "SET max_access_at = expires_at "
        "WHERE max_access_at IS NULL"
    )
    op.execute(
        f'ALTER TABLE "{S}".temp_grants '
        "ALTER COLUMN max_access_seconds SET NOT NULL"
    )
    op.execute(
        f'ALTER TABLE "{S}".temp_grants '
        "ALTER COLUMN max_access_at SET NOT NULL"
    )
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_temp_grants_max_access '
        f'ON "{S}".temp_grants (max_access_at)'
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'DROP INDEX IF EXISTS "{S}".ix_temp_grants_max_access')
    op.execute(
        f'ALTER TABLE "{S}".temp_grants DROP COLUMN IF EXISTS max_access_at'
    )
    op.execute(
        f'ALTER TABLE "{S}".temp_grants DROP COLUMN IF EXISTS max_access_seconds'
    )
