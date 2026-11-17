"""Web 自助接入：首跑初始化、配对码生成/列表/撤销。"""
import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from martin.models import GroupMember, Member, Owner
from martin.security import sha256_hex

pytestmark = pytest.mark.asyncio

PW = "pw123456"


async def _setup_owner(client, app, username="webuser"):
    r = await client.post(
        "/api/v1/web/setup",
        json={"username": username, "password": PW},
    )
    assert r.status_code == 200, r.text
    return r.json()["owner"]


async def test_T_S01_setup_flow(client, app):
    st = await client.get("/api/v1/web/setup-status")
    assert st.json()["needsSetup"] is True

    short = await client.post(
        "/api/v1/web/setup", json={"username": "webuser", "password": "123"}
    )
    assert short.status_code == 422

    await _setup_owner(client, app)
    me = await client.get("/api/v1/web/me")
    assert me.status_code == 200
    assert me.json()["owner"]["displayName"] == "webuser"
    assert me.json()["humanMember"]["id"].startswith("u")

    st2 = await client.get("/api/v1/web/setup-status")
    assert st2.json()["needsSetup"] is False

    again = await client.post(
        "/api/v1/web/setup", json={"username": "other", "password": PW}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_initialized"


async def test_T_S02_pair_code_crud_and_pairing(client, app, fx):
    await _setup_owner(client, app)
    a = await fx.new_member("S-SEED-A", "Signup Bot")
    g = await fx.create_group(a, "signup-group")

    owner = (await fx.db.execute(select(Owner))).scalars().first()
    human = (
        await fx.db.execute(select(Member).where(Member.owner_id == owner.id, Member.human.is_(True)))
    ).scalars().first()
    fx.db.add(
        GroupMember(group_id=await fx.gid(g["id"]), member_id=human.id, role="member")
    )
    await fx.db.commit()

    unauth = httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )
    async with unauth:
        r = await unauth.get("/api/v1/web/pairing-codes")
        assert r.status_code == 401

    bad = await client.post("/api/v1/web/pairing-codes", json={"groupId": "g00000000"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_group"

    r = await client.post("/api/v1/web/pairing-codes", json={"groupId": g["id"]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["code"] and data["instruction"].count(data["code"]) == 1
    assert f"群 id={g['id']}" in data["instruction"]

    paired = await fx.pair(data["code"], "Signup Agent", platform="test")
    assert paired.status_code == 200
    assert paired.json()["join"]["groupId"] == g["id"]

    listed = await client.get("/api/v1/web/pairing-codes")
    used_row = next(c for c in listed.json()["codes"] if c["id"] == data["id"])
    assert used_row["status"] == "used"
    assert used_row["usedAt"] is not None

    r2 = await client.post("/api/v1/web/pairing-codes", json={})
    code2 = r2.json()["code"]
    listed2 = await client.get("/api/v1/web/pairing-codes")
    ids = [c["id"] for c in listed2.json()["codes"]]
    assert r2.json()["id"] in ids
    assert next(c for c in listed2.json()["codes"] if c["id"] == r2.json()["id"])["status"] == "active"

    revoked = await client.delete(f"/api/v1/web/pairing-codes/{r2.json()['id']}")
    assert revoked.json()["revoked"] is True
    listed3 = await client.get("/api/v1/web/pairing-codes")
    rev_row = next(c for c in listed3.json()["codes"] if c["id"] == r2.json()["id"])
    assert rev_row["status"] == "revoked"
    assert rev_row["revokedAt"] is not None
    used = await fx.pair(code2, "Revoked Agent", platform="test")
    assert used.status_code == 403
    assert used.json()["error"]["code"] == "code_used"


async def test_T_S02b_pair_code_history_statuses(client, app, fx):
    """生成历史保留已使用/已过期记录，并按状态返回。"""
    await _setup_owner(client, app)
    owner = (await fx.db.execute(select(Owner))).scalars().first()

    live = await client.post("/api/v1/web/pairing-codes", json={"reclaimName": "Old Bot"})
    assert live.status_code == 200
    expired_code = await fx.make_code("S-EXPIRED", owner_id=owner.id, expired=True)

    codes = (await client.get("/api/v1/web/pairing-codes")).json()["codes"]
    live_row = next(c for c in codes if c["id"] == live.json()["id"])
    assert live_row["status"] == "active"
    assert live_row["reclaimName"] == "Old Bot"

    from sqlalchemy import text

    expired_id = (
        await fx.db.execute(
            text("SELECT id FROM martin.pairing_codes WHERE code_hash=:h"),
            {"h": sha256_hex(expired_code.upper())},
        )
    ).scalar_one()
    assert next(c for c in codes if c["id"] == expired_id)["status"] == "expired"


async def test_T_S03_reclaim_instruction(client, app):
    await _setup_owner(client, app)
    r = await client.post(
        "/api/v1/web/pairing-codes",
        json={"reclaimName": "Signup Bot"},
    )
    assert r.status_code == 200
    instruction = r.json()["instruction"]
    assert "--reclaim" in instruction and '"Signup Bot"' in instruction


async def test_T_S04_register_requires_admin_approval(client, app, fx):
    await _setup_owner(client, app)
    await fx.make_admin("root")

    ua = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with ua:
        reg = await ua.post(
            "/api/v1/web/register", json={"username": "pending1", "password": PW}
        )
        assert reg.status_code == 200
        assert reg.json()["pending"] is True

        login = await ua.post(
            "/api/v1/web/login", json={"username": "pending1", "password": PW}
        )
        assert login.status_code == 403
        assert login.json()["error"]["code"] == "pending_approval"

        admin_login = await ua.post(
            "/api/v1/admin/login", json={"username": "root", "password": PW}
        )
        assert admin_login.status_code == 200, admin_login.text

        users = (await ua.get("/api/v1/admin/users")).json()["users"]
        pending = next(u for u in users if u["displayName"] == "pending1")
        assert pending["approved"] is False

        stats = (await ua.get("/api/v1/admin/stats")).json()["stats"]
        assert stats["pendingOwners"] >= 1

        approved = await ua.post(f"/api/v1/admin/users/{pending['id']}/approve")
        assert approved.status_code == 200 and approved.json()["approved"] is True

        login2 = await ua.post(
            "/api/v1/web/login", json={"username": "pending1", "password": PW}
        )
        assert login2.status_code == 200
        me = await ua.get("/api/v1/web/me")
        assert me.json()["owner"]["displayName"] == "pending1"


async def test_T_S05_register_duplicate_username(client, app):
    await _setup_owner(client, app)
    r1 = await client.post(
        "/api/v1/web/register", json={"username": "dup1", "password": PW}
    )
    assert r1.status_code == 200
    r2 = await client.post(
        "/api/v1/web/register", json={"username": "dup1", "password": PW}
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "owner_exists"


async def test_T_S06_unified_login_by_admin_table(client, app, fx):
    await _setup_owner(client, app)
    await fx.make_admin("root")

    ua = httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    async with ua:
        owner_login = await ua.post(
            "/api/v1/web/session", json={"username": "webuser", "password": PW}
        )
        assert owner_login.status_code == 200
        assert owner_login.json()["kind"] == "owner"
        assert (await ua.get("/api/v1/web/me")).status_code == 200

        admin_login = await ua.post(
            "/api/v1/web/session", json={"username": "root", "password": PW}
        )
        assert admin_login.status_code == 200
        assert admin_login.json()["kind"] == "admin"
        assert (await ua.get("/api/v1/admin/me")).status_code == 200
        # 同一浏览器只保留一个身份：管理员登录后用户会话被清除
        assert (await ua.get("/api/v1/web/me")).status_code == 401

        bad = await ua.post(
            "/api/v1/web/session", json={"username": "root", "password": "nope"}
        )
        assert bad.status_code == 401
        assert bad.json()["error"]["code"] == "invalid_credentials"

        reg = await ua.post(
            "/api/v1/web/register", json={"username": "pending2", "password": PW}
        )
        assert reg.status_code == 200
        blocked = await ua.post(
            "/api/v1/web/session", json={"username": "pending2", "password": PW}
        )
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "pending_approval"
