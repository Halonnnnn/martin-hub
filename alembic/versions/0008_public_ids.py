"""public ids for members and groups

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from martin.config import get_settings
from martin.security import gen_public_id

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill(bind, schema: str, table: str, flag_col: str, prefix) -> None:
    seen: set[str] = set()
    rows = bind.execute(
        sa.text(f'SELECT id, {flag_col} FROM "{schema}"."{table}"')
    ).fetchall()
    for row_id, flag in rows:
        while True:
            code = gen_public_id(prefix(flag))
            if code not in seen:
                break
        seen.add(code)
        bind.execute(
            sa.text(f'UPDATE "{schema}"."{table}" SET public_id = :code WHERE id = :id'),
            {"code": code, "id": row_id},
        )


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'ALTER TABLE "{S}".members ADD COLUMN IF NOT EXISTS public_id VARCHAR(16)')
    op.execute(f'ALTER TABLE "{S}".groups ADD COLUMN IF NOT EXISTS public_id VARCHAR(16)')

    bind = op.get_bind()
    _backfill(bind, S, "members", "human", lambda human: "u" if human else "a")
    _backfill(bind, S, "groups", "kind", lambda _kind: "g")

    op.execute(f'ALTER TABLE "{S}".members ALTER COLUMN public_id SET NOT NULL')
    op.execute(f'ALTER TABLE "{S}".groups ALTER COLUMN public_id SET NOT NULL')
    op.execute(
        f'CREATE UNIQUE INDEX IF NOT EXISTS ux_members_public_id ON "{S}".members (public_id)'
    )
    op.execute(
        f'CREATE UNIQUE INDEX IF NOT EXISTS ux_groups_public_id ON "{S}".groups (public_id)'
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'DROP INDEX IF EXISTS "{S}".ux_members_public_id')
    op.execute(f'DROP INDEX IF EXISTS "{S}".ux_groups_public_id')
    op.execute(f'ALTER TABLE "{S}".groups DROP COLUMN IF EXISTS public_id')
    op.execute(f'ALTER TABLE "{S}".members DROP COLUMN IF EXISTS public_id')
