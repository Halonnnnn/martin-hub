"""temp grants: one-time guest chat authorization

Revision ID: 0013
Revises: 0012
"""
from typing import Sequence, Union

from alembic import op

from martin.models import TempGrant

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    TempGrant.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    TempGrant.__table__.drop(bind, checkfirst=True)
