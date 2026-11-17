"""urgent push: message priority, wake urgent/muted, webhook secret cipher

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    S = get_settings().db_schema
    op.execute(
        f'''ALTER TABLE "{S}".messages
            ADD COLUMN IF NOT EXISTS priority VARCHAR(10) NOT NULL DEFAULT 'normal' '''
    )
    op.execute(
        f'''ALTER TABLE "{S}".wake_queue
            ADD COLUMN IF NOT EXISTS urgent BOOLEAN NOT NULL DEFAULT false'''
    )
    op.execute(
        f'''ALTER TABLE "{S}".wake_events
            ADD COLUMN IF NOT EXISTS urgent BOOLEAN NOT NULL DEFAULT false'''
    )
    op.execute(
        f'''ALTER TABLE "{S}".wake_config
            ADD COLUMN IF NOT EXISTS muted BOOLEAN NOT NULL DEFAULT false'''
    )
    op.execute(
        f'''ALTER TABLE "{S}".wake_config ADD COLUMN IF NOT EXISTS secret_cipher TEXT'''
    )
    op.execute(
        f'''CREATE INDEX IF NOT EXISTS ix_wake_queue_dispatch
            ON "{S}".wake_queue (status, urgent DESC, next_at)'''
    )


def downgrade() -> None:
    S = get_settings().db_schema
    op.execute(f'DROP INDEX IF EXISTS "{S}".ix_wake_queue_dispatch')
    op.execute(f'ALTER TABLE "{S}".wake_config DROP COLUMN IF EXISTS secret_cipher')
    op.execute(f'ALTER TABLE "{S}".wake_config DROP COLUMN IF EXISTS muted')
    op.execute(f'ALTER TABLE "{S}".wake_events DROP COLUMN IF EXISTS urgent')
    op.execute(f'ALTER TABLE "{S}".wake_queue DROP COLUMN IF EXISTS urgent')
    op.execute(f'ALTER TABLE "{S}".messages DROP COLUMN IF EXISTS priority')
