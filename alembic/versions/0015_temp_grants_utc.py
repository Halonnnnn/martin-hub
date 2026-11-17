"""temp grants: 时间列统一成 TIMESTAMPTZ（UTC）

Revision ID: 0015
Revises: 0014

迁移 0013 直接用模型建表；如果服务器上先跑过旧版 0013（或表是手工建的），
temp_grants 的时间列可能是 `timestamp without time zone`，里面存的是
**数据库会话时区下的本地墙钟**。应用按 UTC 读就会整整差一个时区，
表现为「刚进入就到期」。这里把每一列转换成 TIMESTAMPTZ：

- 本地墙钟按 `MARTIN_DB_LOCALTIME_ZONE`（默认 Asia/Shanghai，与 .env 的 TZ 一致）
  解释，迁移里显式 `SET LOCAL TIME ZONE`，不依赖连接默认值；
- 已经是 TIMESTAMPTZ 的列保持不动，可重复执行。
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

from martin.config import get_settings

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

WINDOW_COLUMNS = ("expires_at", "max_access_at")
MOMENT_COLUMNS = ("redeemed_at", "last_seen_at", "revoked_at", "closed_at", "created_at")
COLUMNS = MOMENT_COLUMNS + WINDOW_COLUMNS


def _column_type(bind, schema: str, column: str) -> str | None:
    row = bind.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = 'temp_grants' "
            "AND column_name = :column"
        ),
        {"schema": schema, "column": column},
    ).fetchone()
    return None if row is None else row[0]


def upgrade() -> None:
    settings = get_settings()
    schema = settings.db_schema
    bind = op.get_bind()
    if _column_type(bind, schema, "expires_at") is None:
        return  # 这张表还没建出来（0013 会在之后建）

    zone = settings.db_localtime_zone or "UTC"
    # 用 set_config(..., true)：事务级，不污染连接池里的其他连接
    bind.execute(text("SELECT set_config('TimeZone', :zone, true)"), {"zone": zone})
    for column in COLUMNS:
        if _column_type(bind, schema, column) == "timestamp without time zone":
            op.execute(
                f'ALTER TABLE "{schema}".temp_grants ALTER COLUMN "{column}" '
                f'TYPE TIMESTAMPTZ USING "{column}"::TIMESTAMPTZ'
            )


def downgrade() -> None:
    schema = get_settings().db_schema
    bind = op.get_bind()
    if _column_type(bind, schema, "expires_at") is None:
        return
    for column in COLUMNS:
        if _column_type(bind, schema, column) == "timestamp with time zone":
            op.execute(
                f'ALTER TABLE "{schema}".temp_grants ALTER COLUMN "{column}" '
                f'TYPE TIMESTAMP WITHOUT TIME ZONE USING "{column}"::TIMESTAMP'
            )
