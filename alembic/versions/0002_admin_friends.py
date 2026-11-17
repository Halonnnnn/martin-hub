"""admin role, owner disable, group kind

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{S}"')
    op.execute(
        f'''
        CREATE TABLE IF NOT EXISTS "{S}".admins (
            id BIGSERIAL PRIMARY KEY,
            username VARCHAR(100) NOT NULL UNIQUE,
            password_hash VARCHAR(100) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            disabled_at TIMESTAMPTZ
        )
        '''
    )
    op.execute(f'ALTER TABLE "{S}".owners ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ')
    op.execute(
        f'ALTER TABLE "{S}".groups ADD COLUMN IF NOT EXISTS kind VARCHAR(10) NOT NULL DEFAULT \'group\''
    )
    op.execute(f'CREATE INDEX IF NOT EXISTS ix_groups_kind ON "{S}".groups (kind)')
    op.execute(
        f'CREATE INDEX IF NOT EXISTS ix_friend_requests_to_status ON "{S}".friend_requests (to_member, status)'
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'DROP INDEX IF EXISTS "{S}".ix_friend_requests_to_status')
    op.execute(f'DROP INDEX IF EXISTS "{S}".ix_groups_kind')
    op.execute(f'ALTER TABLE "{S}".groups DROP COLUMN IF EXISTS kind')
    op.execute(f'ALTER TABLE "{S}".owners DROP COLUMN IF EXISTS disabled_at')
    op.execute(f'DROP TABLE IF EXISTS "{S}".admins')