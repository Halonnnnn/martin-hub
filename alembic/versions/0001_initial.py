"""M0 initial tables in martin schema

Revision ID: 0001
Revises:
"""
from typing import Sequence, Union

from alembic import op

from martin.config import get_settings
from martin.models import Base

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    SCHEMA = get_settings().db_schema
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    SCHEMA = get_settings().db_schema
    Base.metadata.drop_all(bind=op.get_bind())
