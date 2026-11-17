import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _set_presence(fx, member: dict, status: str):
    return await fx.client.put(
        "/api/v1/presence", json={"status": status}, headers=member["headers"]
    )


async def test_T_U01_presence_roundtrip(fx):
    a = await fx.new_member("U-A", "Presence Bot")

    r = await fx.client.get("/api/v1/me", headers=a["headers"])
    assert r.status_code == 200
    assert r.json()["member"]["presence"] == "offline"
    assert r.json()["member"]["presenceAt"] is None
    r = await fx.client.get("/api/v1/presence", headers=a["headers"])
    assert r.status_code == 200
    assert r.json()["presence"] == "offline"

    r = await _set_presence(fx, a, "online")
    assert r.status_code == 200, r.text
    assert r.json()["presence"] == "online"
    assert r.json()["member"]["presence"] == "online"
    assert r.json()["updatedAt"]

    r = await fx.client.get("/api/v1/me", headers=a["headers"])
    assert r.json()["member"]["presence"] == "online"
    r = await fx.client.get("/api/v1/presence", headers=a["headers"])
    assert r.json()["presence"] == "online"
    assert r.json()["updatedAt"]

    r = await _set_presence(fx, a, "offline")
    assert r.status_code == 200
    assert r.json()["presence"] == "offline"
    r = await fx.client.get("/api/v1/me", headers=a["headers"])
    assert r.json()["member"]["presence"] == "offline"

    # 只接受 online / offline
    r = await _set_presence(fx, a, "busy")
    assert r.status_code == 422


async def test_T_U02_presence_is_per_member(fx):
    a = await fx.new_member("U-B1", "Online One")
    b = await fx.new_member("U-B2", "Offline Two")

    await _set_presence(fx, a, "online")

    me = (await fx.client.get("/api/v1/me", headers=a["headers"])).json()["member"]
    other = (await fx.client.get("/api/v1/me", headers=b["headers"])).json()["member"]
    assert me["presence"] == "online"
    assert other["presence"] == "offline"


async def test_T_U03_web_agents_and_chat_show_presence(fx):
    owner = await fx.make_owner("changyu")
    agent = await fx.new_member("U-C", "Status Agent", owner_id=owner.id)
    agent_id = agent["member"]["id"]
    assert (await fx.web_login("changyu")).status_code == 200

    # agent 不在好友里；它的在线状态在「接入 Agent」页
    assert (await fx.client.get("/api/v1/web/friends")).json()["friends"] == []
    agents = (await fx.client.get("/api/v1/web/agents")).json()["agents"]
    assert next(a for a in agents if a["id"] == agent_id)["presence"] == "offline"

    r = await fx.client.post("/api/v1/web/chats", json={"with": agent_id})
    assert r.status_code == 200, r.text
    gid = r.json()["group"]["id"]
    assert r.json()["peer"]["presence"] == "offline"

    chats = (await fx.client.get("/api/v1/web/chats")).json()["chats"]
    assert next(c for c in chats if c["id"] == gid)["peer"]["presence"] == "offline"

    await _set_presence(fx, agent, "online")

    agents = (await fx.client.get("/api/v1/web/agents")).json()["agents"]
    assert next(a for a in agents if a["id"] == agent_id)["presence"] == "online"
    chats = (await fx.client.get("/api/v1/web/chats")).json()["chats"]
    assert next(c for c in chats if c["id"] == gid)["peer"]["presence"] == "online"

    # 群成员列表也带状态，非好友/群聊之外的数据源保持一致
    group = await fx.create_group(agent, "presence-room")
    assert (
        await fx.client.post(f"/api/v1/web/groups/{group['id']}/join")
    ).status_code == 200
    members = (
        await fx.client.get(f"/api/v1/web/groups/{group['id']}/members")
    ).json()["members"]
    assert next(m for m in members if m["id"] == agent_id)["presence"] == "online"

    await _set_presence(fx, agent, "offline")
    chats = (await fx.client.get("/api/v1/web/chats")).json()["chats"]
    assert next(c for c in chats if c["id"] == gid)["peer"]["presence"] == "offline"


async def test_T_U04_revoke_token_clears_presence(fx):
    a = await fx.new_member("U-D", "Revoked Bot")
    await _set_presence(fx, a, "online")

    r = await fx.client.delete("/api/v1/token", headers=a["headers"])
    assert r.status_code == 200
    row = (
        await fx.db.execute(
            text("SELECT presence FROM martin.members WHERE public_id=:p"),
            {"p": a["member"]["id"]},
        )
    ).scalar_one()
    assert row == "offline"

    # 吊销后不能再上报
    r = await _set_presence(fx, a, "online")
    assert r.status_code == 401
