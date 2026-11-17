from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import Settings
from .models import Base


def make_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_schema_and_tables(engine: AsyncEngine, schema: str) -> None:
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn))
