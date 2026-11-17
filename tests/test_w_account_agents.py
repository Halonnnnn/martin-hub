"""W 段：自己的 agent 管理、邀请进群、账号设置与会话失效。"""
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def web_sessions(app, *usernames, password="pw123456"):
    clients = []
    try:
        for username in usernames:
            client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
            clients.append(client)
            r = await client.post(
                "/api/v1/web/login",
                json={"username": username, "password": password},
            )
            assert r.status_code == 200, r.text
        yield clients
    finally:
        for client in clients:
            await client.aclose()


async def _agent(fx, code: str, name: str, owner_id: int) -> dict:
    return await fx.new_member(code, name, owner_id=owner_id)


@asynccontextmanager
async def _setup_group(fx, app, owner, agent, group_name="w-group"):
    group = await fx.create_group(agent, group_name, topic="隔离测试")
    async with web_sessions(app, owner.display_name) as (client,):
        r = await client.post(f"/api/v1/web/groups/{group['id']}/join")
        assert r.status_code == 200, r.text
        yield client, group


async def test_T_W01_agents_list_is_scoped_to_owner(fx, app):
    owner_a = await fx.make_owner("alice")
    owner_b = await fx.make_owner("bob")
    a1 = await _agent(fx, "W01-A1", "Alice One", owner_a.id)
    a2 = await _agent(fx, "W01-A2", "Alice Two", owner_a.id)
    await _agent(fx, "W01-B1", "Bob One", owner_b.id)
    group = await fx.create_group(a1, "agents-list")

    async with web_sessions(app, "alice") as (ca,):
        r = await ca.get("/api/v1/web/agents")
        assert r.status_code == 200, r.text
        agents = {x["name"]: x for x in r.json()["agents"]}
        assert set(agents) == {"Alice One", "Alice Two"}
        assert agents["Alice One"]["human"] is False
        assert agents["Alice One"]["label"] == "your agent"
        assert [g["id"] for g in agents["Alice One"]["groups"]] == [group["id"]]
        assert agents["Alice Two"]["groups"] == []
        assert agents["Alice One"]["tokenRevoked"] is False

    async with web_sessions(app, "bob") as (cb,):
        agents = {x["name"] for x in (await cb.get("/api/v1/web/agents")).json()["agents"]}
        assert agents == {"Bob One"}


async def test_T_W02_invite_own_agent_into_group(fx, app):
    owner = await fx.make_owner("alice")
    a1 = await _agent(fx, "W02-A1", "Host Bot", owner.id)
    a2 = await _agent(fx, "W02-A2", "Helper Bot", owner.id)

    async with _setup_group(fx, app, owner, a1, "invite-agent") as (ca, group):
        # 把不在群里的自己的 agent 拉进来
        r = await ca.post(
            f"/api/v1/web/groups/{group['id']}/agents",
            json={"agentId": a2["member"]["id"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["joined"] is True
        assert r.json()["agent"]["name"] == "Helper Bot"

        members = (
            await ca.get(f"/api/v1/web/groups/{group['id']}/members")
        ).json()["members"]
        assert {"alice", "Host Bot", "Helper Bot"} <= {m["name"] for m in members}

        # 系统消息留痕
        msgs = (
            await ca.get(f"/api/v1/web/groups/{group['id']}/messages")
        ).json()["messages"]
        assert any(
            m["type"] == "system" and "邀请 Helper Bot" in (m["text"] or "")
            for m in msgs
        )

        # 幂等
        r = await ca.post(
            f"/api/v1/web/groups/{group['id']}/agents",
            json={"agentId": a2["member"]["id"]},
        )
        assert r.status_code == 200
        assert r.json()["joined"] is False

        # 别人的 agent 不能拉
        bob = await fx.make_owner("bob")
        b1 = await _agent(fx, "W02-B1", "Bob Bot", bob.id)
        r = await ca.post(
            f"/api/v1/web/groups/{group['id']}/agents",
            json={"agentId": b1["member"]["id"]},
        )
        assert r.status_code == 403

        # 私聊里不能拉 agent
        r = await ca.post("/api/v1/web/chats", json={"with": a1["member"]["id"]})
        chat_id = r.json()["group"]["id"]
        r = await ca.post(
            f"/api/v1/web/groups/{chat_id}/agents",
            json={"agentId": a2["member"]["id"]},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_group"


async def test_T_W03_invite_requires_membership(fx, app):
    owner_a = await fx.make_owner("alice")
    owner_b = await fx.make_owner("bob")
    a2 = await _agent(fx, "W03-A2", "Alice Helper", owner_a.id)
    b1 = await _agent(fx, "W03-B1", "Bob Host", owner_b.id)
    await fx.create_group(b1, "bob-room")

    async with web_sessions(app, "alice") as (ca,):
        # alice 不在 bob 的群里，不能拉自己的 agent 进去
        bob_group_id = (
            await fx.db.execute(
                text(
                    "SELECT public_id FROM martin.groups WHERE name='bob-room'"
                )
            )
        ).scalar_one()
        r = await ca.post(
            f"/api/v1/web/groups/{bob_group_id}/agents",
            json={"agentId": a2["member"]["id"]},
        )
        assert r.status_code == 403


async def test_T_W04_group_member_limit(fx, app):
    owner = await fx.make_owner("alice")
    a1 = await _agent(fx, "W04-A1", "Limit Host", owner.id)
    a2 = await _agent(fx, "W04-A2", "Limit Extra", owner.id)
    group = await fx.create_group(a1, "limited")

    # 群主 agent + alice 已占满 2 个名额
    app.state.settings.members_per_group_limit = 2
    async with web_sessions(app, "alice") as (ca,):
        assert (await ca.post(f"/api/v1/web/groups/{group['id']}/join")).status_code == 200
        r = await ca.post(
            f"/api/v1/web/groups/{group['id']}/agents",
            json={"agentId": a2["member"]["id"]},
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "group_full"


async def test_T_W05_removed_agent_can_rejoin(fx, app):
    owner = await fx.make_owner("alice")
    a1 = await _agent(fx, "W05-A1", "Rejoin Host", owner.id)
    a2 = await _agent(fx, "W05-A2", "Rejoin Guest", owner.id)
    group = await fx.create_group(a1, "rejoin")

    async with web_sessions(app, "alice") as (ca,):
        assert (await ca.post(f"/api/v1/web/groups/{group['id']}/join")).status_code == 200
        r = await ca.post(
            f"/api/v1/web/groups/{group['id']}/agents",
            json={"agentId": a2["member"]["id"]},
        )
        assert r.status_code == 200 and r.json()["joined"] is True

        # 模拟被群主移出后，owner 重新拉回
        await fx.db.execute(
            text(
                "UPDATE martin.group_members SET removed_at=now() "
                "WHERE group_id=:g AND member_id=:m"
            ),
            {"g": await fx.gid(group["id"]), "m": await fx.mid(a2["member"]["id"])},
        )
        await fx.db.commit()
        r = await ca.post(
            f"/api/v1/web/groups/{group['id']}/agents",
            json={"agentId": a2["member"]["id"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["joined"] is True


async def test_T_W06_change_password_logs_out_everywhere(fx, app):
    await fx.make_owner("alice")
    async with web_sessions(app, "alice", "alice") as (c1, c2):
        r = await c1.post(
            "/api/v1/web/password",
            json={"currentPassword": "wrong", "newPassword": "newpw123"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_password"

        r = await c1.post(
            "/api/v1/web/password",
            json={"currentPassword": "pw123456", "newPassword": "short"},
        )
        assert r.status_code == 422

        r = await c1.post(
            "/api/v1/web/password",
            json={"currentPassword": "pw123456", "newPassword": "newpw123"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["relogin"] is True

        # 两个浏览器都立即失效
        assert (await c1.get("/api/v1/web/me")).status_code == 401
        assert (await c2.get("/api/v1/web/me")).status_code == 401

    # 旧密码失效，新密码可登录
    assert (await fx.web_login("alice", "pw123456")).status_code == 401
    assert (await fx.web_login("alice", "newpw123")).status_code == 200


async def test_T_W07_admin_reset_and_disable_invalidate_sessions(fx, app):
    await fx.make_admin("root")
    assert (await fx.admin_login("root")).status_code == 200
    owner = await fx.make_owner("alice")
    async with web_sessions(app, "alice") as (ca,):
        user_id = owner.id
        # 管理员重置密码
        r = await fx.client.post(
            f"/api/v1/admin/users/{user_id}/password", json={"password": "reset123"}
        )
        assert r.status_code == 200
        assert (await ca.get("/api/v1/web/me")).status_code == 401
        assert (await fx.web_login("alice", "reset123")).status_code == 200

        # 管理员停用后再启用，旧会话仍不能用
        async with web_sessions(app, "alice", password="reset123") as (cb,):
            r = await fx.client.post(f"/api/v1/admin/users/{user_id}/disable")
            assert r.status_code == 200
            assert (await cb.get("/api/v1/web/me")).status_code == 401
            r = await fx.client.post(f"/api/v1/admin/users/{user_id}/enable")
            assert r.status_code == 200
            assert (await cb.get("/api/v1/web/me")).status_code == 401
            assert (await fx.web_login("alice", "reset123")).status_code == 200
