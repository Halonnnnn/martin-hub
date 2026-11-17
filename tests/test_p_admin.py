import pytest

pytestmark = pytest.mark.asyncio


async def test_T_P01_admin_login(fx):
    await fx.make_admin("root", "s3cret!")

    r = await fx.admin_login("root", "wrong")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"

    r = await fx.admin_login("root", "s3cret!")
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert "martin_admin_session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert r.json()["admin"]["username"] == "root"

    r = await fx.client.get("/api/v1/admin/me")
    assert r.status_code == 200
    assert r.json()["admin"]["username"] == "root"


async def test_T_P01b_disabled_admin_login(fx):
    from martin.admin import set_admin_disabled

    from tests.conftest import TEST_URL

    await fx.make_admin("root")
    await set_admin_disabled("root", True, database_url=TEST_URL)
    r = await fx.admin_login("root")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "admin_disabled"


async def test_T_P02_auth_isolation(fx, app):
    await fx.make_admin("root")
    owner = await fx.make_owner("changyu")
    agent = await fx.new_member("P02-CODE", "Bot", owner_id=owner.id)

    # admin cookie only -> admin endpoints work, web endpoints rejected
    assert (await fx.admin_login("root")).status_code == 200
    assert (await fx.client.get("/api/v1/admin/users")).status_code == 200
    assert (await fx.client.get("/api/v1/web/me")).status_code == 401

    # owner cookie only -> web works, admin rejected
    fx.client.cookies.clear()
    assert (await fx.web_login("changyu")).status_code == 200
    assert (await fx.client.get("/api/v1/web/me")).status_code == 200
    assert (await fx.client.get("/api/v1/admin/users")).status_code == 401

    # agent bearer only -> neither cookie domain accessible
    fx.client.cookies.clear()
    assert (
        await fx.client.get("/api/v1/admin/users", headers=agent["headers"])
    ).status_code == 401
    assert (
        await fx.client.get("/api/v1/web/me", headers=agent["headers"])
    ).status_code == 401
    assert (await fx.client.get("/api/v1/me", headers=agent["headers"])).status_code == 200


async def test_T_P03_create_user(fx):
    await fx.make_admin("root")
    await fx.admin_login("root")

    r = await fx.client.post(
        "/api/v1/admin/users", json={"username": "alice", "password": "pw123456"}
    )
    assert r.status_code == 200
    user = r.json()["user"]
    assert user["displayName"] == "alice"
    assert user["humanMember"] == "alice"
    assert user["agentCount"] == 0
    assert user["disabled"] is False

    # new owner can log in immediately
    assert (await fx.web_login("alice")).status_code == 200

    # duplicate owner name rejected
    r = await fx.client.post(
        "/api/v1/admin/users", json={"username": "alice", "password": "pw123456"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "owner_exists"


async def test_T_P04_reset_password(fx):
    await fx.make_admin("root")
    owner = await fx.make_owner("carol", "oldpw123")
    await fx.admin_login("root")

    r = await fx.client.post(
        f"/api/v1/admin/users/{owner.id}/password", json={"password": "newpw123"}
    )
    assert r.status_code == 200

    fx.client.cookies.clear()
    assert (await fx.web_login("carol", "oldpw123")).status_code == 401
    assert (await fx.web_login("carol", "newpw123")).status_code == 200


async def test_T_P05_disable_cascade(fx):
    await fx.make_admin("root")
    owner = await fx.make_owner("changyu")
    agent = await fx.new_member("P05-CODE", "Bot", owner_id=owner.id)

    # warm the token cache, then confirm agent works
    assert (await fx.client.get("/api/v1/me", headers=agent["headers"])).status_code == 200

    await fx.admin_login("root")
    r = await fx.client.post(f"/api/v1/admin/users/{owner.id}/disable")
    assert r.status_code == 200

    # owner web login blocked
    r = await fx.web_login("changyu")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "owner_disabled"
    # agent token invalidated by cascade (even with warm cache)
    assert (await fx.client.get("/api/v1/me", headers=agent["headers"])).status_code == 401

    # enable restores both
    r = await fx.client.post(f"/api/v1/admin/users/{owner.id}/enable")
    assert r.status_code == 200
    assert (await fx.web_login("changyu")).status_code == 200
    assert (await fx.client.get("/api/v1/me", headers=agent["headers"])).status_code == 200


async def test_T_P06_revoke_tokens(fx):
    await fx.make_admin("root")
    owner = await fx.make_owner("changyu")
    a = await fx.new_member("P06-A", "Bot A", owner_id=owner.id)
    b = await fx.new_member("P06-B", "Bot B", owner_id=owner.id)
    assert (await fx.client.get("/api/v1/me", headers=a["headers"])).status_code == 200

    await fx.admin_login("root")
    r = await fx.client.post(f"/api/v1/admin/users/{owner.id}/revoke-tokens")
    assert r.status_code == 200
    assert r.json()["revoked"] == 2

    assert (await fx.client.get("/api/v1/me", headers=a["headers"])).status_code == 401
    assert (await fx.client.get("/api/v1/me", headers=b["headers"])).status_code == 401
    # human member has no token; owner web login still works
    assert (await fx.web_login("changyu")).status_code == 200


async def test_T_P07_stats(fx):
    await fx.make_admin("root")
    await fx.admin_login("root")
    await fx.client.post(
        "/api/v1/admin/users", json={"username": "alice", "password": "pw123456"}
    )
    bob = await fx.make_owner("bob")
    a = await fx.new_member("P07-A", "Bot A", owner_id=bob.id)
    b = await fx.new_member("P07-B", "Bot B", owner_id=bob.id)
    await fx.create_group(a, "stats-group")
    # same-owner agents can open a direct chat without friendship
    r = await fx.client.post(
        "/api/v1/chats", json={"with": b["member"]["id"]}, headers=a["headers"]
    )
    assert r.status_code == 200

    r = await fx.client.get("/api/v1/admin/stats")
    assert r.status_code == 200
    s = r.json()["stats"]
    assert s["owners"] == 2
    assert s["admins"] == 1
    assert s["humans"] == 2
    assert s["agents"] == 2
    assert s["groups"] == 1
    assert s["directChats"] == 1


async def test_T_P08_admin_cli_functions(fx):
    from martin.admin import create_admin, list_admins, set_admin_disabled

    from tests.conftest import TEST_URL

    url = TEST_URL
    assert await create_admin("cli1", "pw123456", database_url=url) == {"admin": "cli1"}
    with pytest.raises(SystemExit):
        await create_admin("cli1", "other", database_url=url)

    admins = await list_admins(database_url=url)
    assert {"username": "cli1", "disabled": False} in admins

    assert await set_admin_disabled("cli1", True, database_url=url) == {
        "admin": "cli1",
        "disabled": True,
    }
    assert {"username": "cli1", "disabled": True} in await list_admins(database_url=url)
    await set_admin_disabled("cli1", False, database_url=url)
    assert {"username": "cli1", "disabled": False} in await list_admins(database_url=url)

    with pytest.raises(SystemExit):
        await set_admin_disabled("missing", True, database_url=url)


async def test_T_P09_admin_me_unauthorized(fx):
    r = await fx.client.get("/api/v1/admin/me")
    assert r.status_code == 401
    r = await fx.client.get("/api/v1/admin/users")
    assert r.status_code == 401


async def test_T_P10_users_list_agent_counts(fx):
    await fx.make_admin("root")
    owner = await fx.make_owner("changyu")
    await fx.new_member("P10-A", "Bot A", owner_id=owner.id)
    await fx.new_member("P10-B", "Bot B", owner_id=owner.id)
    await fx.admin_login("root")

    r = await fx.client.get("/api/v1/admin/users")
    assert r.status_code == 200
    users = {u["displayName"]: u for u in r.json()["users"]}
    assert users["changyu"]["agentCount"] == 2
    assert users["changyu"]["humanMember"] == "changyu"