"""P0 hardening: message request_hash, receiver leases

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(
        f'''ALTER TABLE "{S}".messages
            ADD COLUMN IF NOT EXISTS request_hash VARCHAR(64)'''
    )
    op.execute(
        f'''CREATE TABLE IF NOT EXISTS "{S}".receiver_leases (
            member_id BIGINT PRIMARY KEY
                REFERENCES "{S}".members(id),
            holder VARCHAR(80) NOT NULL,
            fence VARCHAR(100) NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL
        )'''
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'DROP TABLE IF EXISTS "{S}".receiver_leases')
    op.execute(f'ALTER TABLE "{S}".messages DROP COLUMN IF EXISTS request_hash')
