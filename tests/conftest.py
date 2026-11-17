import asyncio
import datetime as dt

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from martin.config import Settings
from martin.db import create_schema_and_tables
from martin.main import create_app
from martin.models import Base, Member, Owner, PairingCode
from martin.security import hash_password, sha256_hex

TEST_DB = "groupchat_test"
ADMIN_URL = "postgresql+asyncpg://localhost:5432/postgres"
TEST_URL = f"postgresql+asyncpg://localhost:5432/{TEST_DB}"


def make_settings(**overrides) -> Settings:
    base = dict(
        database_url=TEST_URL,
        secret_key="test-secret-key",
        cache_backend="memory",
        # 默认关闭自动派发，保证断言确定性；需要时由用例显式打开或直接调 run_once
        wake_dispatch_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


@pytest_asyncio.fixture
async def engine():
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await conn.exec_driver_sql(f"CREATE DATABASE {TEST_DB}")
    await admin.dispose()

    engine = create_async_engine(TEST_URL)
    await create_schema_and_tables(engine, "martin")
    yield engine

    await engine.dispose()
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await admin.dispose()


@pytest_asyncio.fixture
async def app(engine, request):
    overrides = getattr(request, "param", {}) or {}
    application = create_app(make_settings(**overrides), ensure_schema=False)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db(app):
    maker = app.state.sessionmaker
    async with maker() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(engine):
    yield
    tables = ", ".join(
        f'"martin"."{t.name}"' for t in sorted(Base.metadata.tables.values(), key=lambda t: t.name)
    )
    if tables:
        async with engine.begin() as conn:
            await conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")


class Fixtures:
    def __init__(self, client, db):
        self.client = client
        self.db = db

    async def make_code(self, code: str, owner_id: int | None = None, group_id: int | None = None, expired: bool = False) -> str:
        expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=-1 if expired else 24)
        self.db.add(
            PairingCode(
                code_hash=sha256_hex(code.upper()),
                owner_id=owner_id,
                group_id=group_id,
                expires_at=expires,
            )
        )
        await self.db.commit()
        return code

    async def make_owner(self, username: str, password: str = "pw123456") -> Owner:
        owner = Owner(
            display_name=username,
            password_hash=hash_password(password),
            approved_at=dt.datetime.now(dt.timezone.utc),
        )
        self.db.add(owner)
        await self.db.flush()
        human = Member(name=username, platform="web", owner_id=owner.id, human=True)
        self.db.add(human)
        await self.db.commit()
        return owner

    async def pair(self, code: str, name: str, platform: str = "test", reclaim: bool = False):
        return await self.client.post(
            "/api/v1/pair",
            json={"code": code, "name": name, "platform": platform, "reclaim": reclaim},
        )

    async def new_member(self, code: str, name: str, platform: str = "test", owner_id: int | None = None, group_id: int | None = None) -> dict:
        await self.make_code(code, owner_id=owner_id, group_id=group_id)
        r = await self.pair(code, name, platform)
        assert r.status_code == 200, r.text
        data = r.json()
        data["headers"] = {"Authorization": f"Bearer {data['token']}"}
        return data

    async def create_group(self, member: dict, name: str, **kwargs) -> dict:
        r = await self.client.post(
            "/api/v1/groups", json={"name": name, **kwargs}, headers=member["headers"]
        )
        assert r.status_code == 200, r.text
        return r.json()["group"]

    async def send(self, member: dict, group_id: int, text: str, **kwargs):
        return await self.client.post(
            f"/api/v1/groups/{group_id}/messages",
            json={"text": text, **kwargs},
            headers=member["headers"],
        )

    async def read(self, member: dict, group_id: int, **params):
        return await self.client.get(
            f"/api/v1/groups/{group_id}/messages", params=params, headers=member["headers"]
        )

    async def gid(self, public_id: str) -> int:
        return (
            await self.db.execute(
                text("SELECT id FROM martin.groups WHERE public_id=:p"), {"p": public_id}
            )
        ).scalar_one()

    async def mid(self, public_id: str) -> int:
        return (
            await self.db.execute(
                text("SELECT id FROM martin.members WHERE public_id=:p"), {"p": public_id}
            )
        ).scalar_one()

    async def web_login(self, username: str, password: str = "pw123456"):
        return await self.client.post(
            "/api/v1/web/login", json={"username": username, "password": password}
        )

    async def make_admin(self, username: str, password: str = "pw123456"):
        from martin.admin import create_admin

        await create_admin(username, password, database_url=TEST_URL)

    async def admin_login(self, username: str, password: str = "pw123456"):
        return await self.client.post(
            "/api/v1/admin/login", json={"username": username, "password": password}
        )


@pytest_asyncio.fixture
async def fx(client, db):
    return Fixtures(client, db)
