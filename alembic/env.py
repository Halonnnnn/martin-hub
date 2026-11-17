import asyncio
from logging.config import fileConfig

from alembic import context

from martin.config import get_settings
from martin.db import make_engine
from martin.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
target_metadata = Base.metadata
SCHEMA = settings.db_schema

# 版本表放在 martin schema 下
version_table_config = {"version_table_schema": SCHEMA}


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = make_engine(settings)
    async with engine.connect() as connection:
        await connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
