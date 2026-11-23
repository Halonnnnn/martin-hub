import pytest

pytestmark = pytest.mark.asyncio


async def test_T_J01_web_login(fx):
    await fx.make_owner("changyu", "s3cret!")

    r = await fx.client.post(
        "/api/v1/web/login", json={"username": "changyu", "password": "wrong"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"

    r = await fx.web_login("changyu", "s3cret!")
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert "martin_session=" in set_cookie
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower()

    r = await fx.client.get("/api/v1/web/me")
    assert r.status_code == 200
    assert r.json()["owner"]["displayName"] == "changyu"
    assert r.json()["humanMember"]["name"] == "changyu"


async def test_T_J17_web_ui_theme(fx):
    await fx.make_owner("changyu", "s3cret!")
    await fx.web_login("changyu", "s3cret!")

    r = await fx.client.get("/api/v1/web/me")
    assert r.status_code == 200
    assert r.json()["owner"]["uiTheme"] is None

    r = await fx.client.patch("/api/v1/web/me", json={"uiTheme": "paper"})
    assert r.status_code == 200
    assert r.json()["owner"]["uiTheme"] == "paper"

    r = await fx.client.get("/api/v1/web/me")
    assert r.json()["owner"]["uiTheme"] == "paper"

    r = await fx.client.patch("/api/v1/web/me", json={"uiTheme": "neon"})
    assert r.status_code == 422

    r = await fx.client.patch("/api/v1/web/me", json={"uiTheme": "dark"})
    assert r.status_code == 200
    assert r.json()["owner"]["uiTheme"] == "dark"


async def test_T_J18_web_logout(fx):
    await fx.make_owner("changyu", "s3cret!")
    await fx.web_login("changyu", "s3cret!")

    r = await fx.client.post("/api/v1/web/logout")
    assert r.status_code == 200
    r = await fx.client.get("/api/v1/web/me")
    assert r.status_code == 401


async def test_T_J19_web_group_member_manage(fx):
    await fx.make_owner("alice", "s3cret!")
    await fx.web_login("alice", "s3cret!")

    r = await fx.client.post("/api/v1/web/groups", json={"name": "g1"})
    assert r.status_code == 200
    gid = r.json()["group"]["id"]
    me = (await fx.client.get("/api/v1/web/me")).json()["humanMember"]["id"]

    # 群主不能退出
    r = await fx.client.post(f"/api/v1/web/groups/{gid}/leave")
    assert r.status_code == 400
    # 移出自己走退出按钮
    r = await fx.client.delete(f"/api/v1/web/groups/{gid}/members/{me}")
    assert r.status_code == 400

    # 接入一个 agent 并加入群，群主可以移出
    code = (await fx.client.post("/api/v1/web/pairing-codes", json={})).json()["code"]
    agent_id = (
        await fx.client.post("/api/v1/pair", json={"code": code, "name": "neo"})
    ).json()["member"]["id"]
    r = await fx.client.post(
        f"/api/v1/web/groups/{gid}/agents", json={"agentId": agent_id}
    )
    assert r.status_code == 200

    r = await fx.client.delete(f"/api/v1/web/groups/{gid}/members/{agent_id}")
    assert r.status_code == 200
    members = (
        await fx.client.get(f"/api/v1/web/groups/{gid}/members")
    ).json()["members"]
    assert all(m["name"] != "neo" for m in members)


async def test_T_J20_web_group_archive_dissolve(fx):
    await fx.make_owner("alice", "s3cret!")
    await fx.web_login("alice", "s3cret!")

    gid = (
        await fx.client.post("/api/v1/web/groups", json={"name": "g1"})
    ).json()["group"]["id"]
    r = await fx.client.post(
        f"/api/v1/web/groups/{gid}/messages", json={"text": "hi"}
    )
    assert r.status_code == 200

    r = await fx.client.post(
        f"/api/v1/web/groups/{gid}/archive", json={"archived": True}
    )
    assert r.status_code == 200
    r = await fx.client.post(
        f"/api/v1/web/groups/{gid}/messages", json={"text": "again"}
    )
    assert r.status_code == 403
    groups = (await fx.client.get("/api/v1/web/groups")).json()["groups"]
    assert [g["archived"] for g in groups if g["id"] == gid] == [True]

    r = await fx.client.post(
        f"/api/v1/web/groups/{gid}/archive", json={"archived": False}
    )
    assert r.json()["archived"] is False
    r = await fx.client.post(
        f"/api/v1/web/groups/{gid}/messages", json={"text": "again"}
    )
    assert r.status_code == 200

    r = await fx.client.delete(f"/api/v1/web/groups/{gid}")
    assert r.status_code == 200
    groups = (await fx.client.get("/api/v1/web/groups")).json()["groups"]
    assert all(g["id"] != gid for g in groups)


async def test_T_J21_web_kick_user_with_agents(fx):
    await fx.make_owner("alice", "s3cret!")
    await fx.make_owner("bob", "s3cret!")
    await fx.web_login("alice", "s3cret!")

    gid = (
        await fx.client.post("/api/v1/web/groups", json={"name": "g1"})
    ).json()["group"]["id"]
    invite = (
        await fx.client.get("/api/v1/web/groups")
    ).json()["groups"][0]["inviteLink"].rsplit("/", 1)[-1]

    await fx.web_login("bob", "s3cret!")
    bob_id = (await fx.client.get("/api/v1/web/me")).json()["humanMember"]["id"]
    assert (
        await fx.client.post("/api/v1/web/join", json={"invite": invite})
    ).status_code == 200
    code = (
        await fx.client.post("/api/v1/web/pairing-codes", json={})
    ).json()["code"]
    bobby = (
        await fx.client.post("/api/v1/pair", json={"code": code, "name": "bobby"})
    ).json()["member"]["id"]
    assert (
        await fx.client.post(
            f"/api/v1/web/groups/{gid}/agents", json={"agentId": bobby}
        )
    ).status_code == 200

    await fx.web_login("alice", "s3cret!")
    r = await fx.client.delete(
        f"/api/v1/web/groups/{gid}/members/{bob_id}?with_agents=true"
    )
    assert r.status_code == 200
    assert r.json()["removedAgents"] == 1
    members = (
        await fx.client.get(f"/api/v1/web/groups/{gid}/members")
    ).json()["members"]
    names = {m["name"] for m in members}
    assert "bob" not in names and "bobby" not in names


async def test_T_J02_admin_init_bcrypt(fx, app):
    from sqlalchemy import text

    from martin.admin import admin_init

    result = await admin_init("alice", "pw123456", database_url=str(app.state.settings.database_url))
    assert result == {"owner": "alice", "humanMember": "alice"}

    row = await fx.db.execute(
        text("SELECT password_hash FROM martin.owners WHERE display_name='alice'")
    )
    h = row.scalar_one()
    assert h.startswith("$2")

    import pytest as _pytest

    with _pytest.raises(SystemExit):
        await admin_init("alice", "other", database_url=str(app.state.settings.database_url))


async def test_T_J03_session_expiry(app, engine):
    settings_overrides = {"web_session_ttl": -1}
    app2_app = create_app_with(settings_overrides)
    from httpx import ASGITransport, AsyncClient

    from martin.admin import admin_init

    await admin_init("bob", "pw123456", database_url=str(app.state.settings.database_url))

    async with AsyncClient(transport=ASGITransport(app=app2_app), base_url="http://t") as c:
        r = await c.post("/api/v1/web/login", json={"username": "bob", "password": "pw123456"})
        assert r.status_code == 200
        r = await c.get("/api/v1/web/me")
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "unauthorized"


def create_app_with(overrides):
    from tests.conftest import make_settings

    from martin.main import create_app as _ca

    return _ca(make_settings(**overrides), ensure_schema=False)


async def test_T_J04_owner_post_wakes_agents(fx, app):
    from sqlalchemy import text

    owner = await fx.make_owner("changyu")
    a = await fx.new_member("J04-CODA", "Sleepy Bot", owner_id=owner.id)
    g = await fx.create_group(a, "wake-me")
    login = await fx.web_login("changyu")
    assert login.status_code == 200
    join = await fx.client.post(f"/api/v1/web/groups/{g['id']}/join")
    assert join.status_code == 200

    r = await fx.client.post(
        f"/api/v1/web/groups/{g['id']}/messages",
        json={"text": "开工了"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["message"]["author"]["human"] is True

    rows = await fx.db.execute(
        text(
            "SELECT reason, status FROM martin.wake_queue WHERE member_id=:m AND group_id=:g"
        ),
        {"m": await fx.mid(a["member"]["id"]), "g": await fx.gid(g["id"])},
    )
    wakes = rows.all()
    assert any(reason == "owner_post" and status == "pending" for reason, status in wakes)


async def test_T_J09_web_members_requires_login(fx):
    r = await fx.client.get("/api/v1/web/groups/1/members")
    assert r.status_code == 401


async def test_T_J10_web_members_non_member_forbidden(fx):
    owner = await fx.make_owner("changyu")
    a = await fx.new_member("J10-CODA", "Sleepy Bot", owner_id=owner.id)
    g = await fx.create_group(a, "members-only")
    login = await fx.web_login("changyu")
    assert login.status_code == 200

    r = await fx.client.get(f"/api/v1/web/groups/{g['id']}/members")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


async def test_T_J11_web_members_lists_human_and_agents(fx):
    owner = await fx.make_owner("changyu")
    a = await fx.new_member("J11-CODA", "Sleepy Bot", owner_id=owner.id)
    g = await fx.create_group(a, "member-list")
    assert (await fx.web_login("changyu")).status_code == 200
    assert (await fx.client.post(f"/api/v1/web/groups/{g['id']}/join")).status_code == 200

    r = await fx.client.get(f"/api/v1/web/groups/{g['id']}/members")
    assert r.status_code == 200
    members = r.json()["members"]
    by_name = {m["name"]: m for m in members}
    assert by_name["changyu"]["human"] is True
    assert by_name["changyu"]["role"] == "member"
    assert by_name["Sleepy Bot"]["human"] is False
    assert by_name["Sleepy Bot"]["role"] == "owner"


async def test_T_J12_web_send_mentions(fx):
    from sqlalchemy import text

    owner = await fx.make_owner("changyu")
    a = await fx.new_member("J12-CODA", "Sleepy Bot", owner_id=owner.id)
    g = await fx.create_group(a, "mention-test")
    assert (await fx.web_login("changyu")).status_code == 200
    assert (await fx.client.post(f"/api/v1/web/groups/{g['id']}/join")).status_code == 200

    r = await fx.client.post(
        f"/api/v1/web/groups/{g['id']}/messages",
        json={"text": "@Sleepy Bot 你好", "mentions": ["Sleepy Bot"]},
    )
    assert r.status_code == 200
    assert "Sleepy Bot" in r.json()["mentioned"]

    row = await fx.db.execute(
        text("SELECT mentions FROM martin.messages WHERE group_id=:g ORDER BY seq DESC LIMIT 1"),
        {"g": await fx.gid(g["id"])},
    )
    assert "Sleepy Bot" in row.scalar_one()

    rows = await fx.db.execute(
        text(
            "SELECT reason FROM martin.wake_queue WHERE member_id=:m AND group_id=:g"
        ),
        {"m": await fx.mid(a["member"]["id"]), "g": await fx.gid(g["id"])},
    )
    assert "owner_post" in [r[0] for r in rows.all()]


async def test_T_J14_web_read_clears_unread_group(fx):
    owner = await fx.make_owner("changyu")
    a = await fx.new_member("J14-CODA", "Reader Bot", owner_id=owner.id)
    g = await fx.create_group(a, "unread-clear")
    assert (await fx.web_login("changyu")).status_code == 200
    assert (await fx.client.post(f"/api/v1/web/groups/{g['id']}/join")).status_code == 200

    assert (await fx.send(a, g["id"], "hello")).status_code == 200
    groups = (await fx.client.get("/api/v1/web/groups")).json()["groups"]
    assert next(x for x in groups if x["id"] == g["id"])["unread"] == 1

    assert (await fx.client.get(f"/api/v1/web/groups/{g['id']}/messages")).status_code == 200
    groups = (await fx.client.get("/api/v1/web/groups")).json()["groups"]
    assert next(x for x in groups if x["id"] == g["id"])["unread"] == 0


async def test_T_J16_web_create_group(fx):
    await fx.make_owner("changyu")
    assert (await fx.web_login("changyu")).status_code == 200

    r = await fx.client.post(
        "/api/v1/web/groups", json={"name": "web-made", "topic": "页面建群"}
    )
    assert r.status_code == 200, r.text
    gid = r.json()["group"]["id"]

    groups = (await fx.client.get("/api/v1/web/groups")).json()["groups"]
    assert any(g["id"] == gid and g["name"] == "web-made" for g in groups)
    members = (await fx.client.get(f"/api/v1/web/groups/{gid}/members")).json()["members"]
    assert any(m["name"] == "changyu" and m["role"] == "owner" for m in members)

    dup = await fx.client.post("/api/v1/web/groups", json={"name": "web-made"})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "group_exists"


async def test_T_J15_web_read_clears_unread_direct(fx):
    owner = await fx.make_owner("changyu")
    a = await fx.new_member("J15-CODA", "DM Bot", owner_id=owner.id)
    assert (await fx.web_login("changyu")).status_code == 200

    r = await fx.client.post("/api/v1/web/chats", json={"with": a["member"]["id"]})
    assert r.status_code == 200, r.text
    gid = r.json()["group"]["id"]
    assert (await fx.send(a, gid, "dm")).status_code == 200
    chats = (await fx.client.get("/api/v1/web/chats")).json()["chats"]
    assert next(x for x in chats if x["id"] == gid)["unread"] == 1

    assert (await fx.client.get(f"/api/v1/web/groups/{gid}/messages")).status_code == 200
    chats = (await fx.client.get("/api/v1/web/chats")).json()["chats"]
    assert next(x for x in chats if x["id"] == gid)["unread"] == 0
