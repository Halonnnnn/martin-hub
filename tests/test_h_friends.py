"""H 段：好友只连接用户；私聊只有用户↔好友与用户↔自己的 agent。"""
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


@asynccontextmanager
async def _two_users(fx, app, first="alice", second="bob"):
    await fx.make_owner(first)
    await fx.make_owner(second)
    async with web_sessions(app, first, second) as (ca, cb):
        a_id = (await ca.get("/api/v1/web/me")).json()["humanMember"]["id"]
        b_id = (await cb.get("/api/v1/web/me")).json()["humanMember"]["id"]
        yield ca, cb, a_id, b_id


async def _request(ca, to: str, note: str = ""):
    r = await ca.post("/api/v1/web/friends", json={"to": to, "note": note})
    assert r.status_code == 200, r.text
    return r.json()["request"]


async def _befriend(ca, cb, a_id: str, b_id: str):
    req = await _request(ca, b_id)
    r = await cb.post(
        f"/api/v1/web/friend-requests/{req['id']}",
        json={"action": "accept"},
    )
    assert r.status_code == 200, r.text
    return req


async def _friend_names(client) -> set[str]:
    r = await client.get("/api/v1/web/friends")
    assert r.status_code == 200, r.text
    return {f["name"] for f in r.json()["friends"]}


async def _agent(fx, code: str, name: str, owner_id: int) -> dict:
    return await fx.new_member(code, name, owner_id=owner_id)


async def test_T_H01_send_request_no_wake(fx, app):
    async with _two_users(fx, app) as (ca, cb, a_id, b_id):
        req = await _request(ca, b_id, "认识一下")
        assert req["status"] == "pending"
        assert req["from"]["name"] == "alice"
        assert req["from"]["human"] is True
        assert req["note"] == "认识一下"
        count = (await fx.db.execute(text("SELECT count(*) FROM martin.wake_queue"))).scalar()
        assert count == 0
        assert await _friend_names(cb) == set()


async def test_T_H02_friend_targets_must_be_users(fx, app):
    async with _two_users(fx, app) as (ca, cb, a_id, b_id):
        owner = (await fx.db.execute(text("SELECT id FROM martin.owners WHERE display_name='alice'"))).scalar_one()
        own_agent = await _agent(fx, "H02-A", "Alice Bot", owner)
        other_agent = await _agent(fx, "H02-B", "Bob Bot", owner)

        # agent 不能发好友请求
        r = await fx.client.post(
            "/api/v1/friends",
            json={"to": b_id, "note": ""},
            headers=own_agent["headers"],
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "friends_are_people"

        # 也不能把人/别人的 agent 加为好友
        for target in (own_agent["member"]["id"], other_agent["member"]["id"]):
            r = await ca.post("/api/v1/web/friends", json={"to": target, "note": ""})
            assert r.status_code == 403, r.text
            assert r.json()["error"]["code"] == "friends_are_people"

        # 按用户名加人也可以
        req = await _request(ca, "bob")
        assert req["status"] == "pending"
        assert req["from"]["name"] == "alice"


async def test_T_H03_accept_bilateral(fx, app):
    async with _two_users(fx, app) as (ca, cb, a_id, b_id):
        await _befriend(ca, cb, a_id, b_id)
        assert await _friend_names(ca) == {"bob"}
        assert await _friend_names(cb) == {"alice"}
        # 好友请求列表清空
        assert (await cb.get("/api/v1/web/friend-requests")).json()["requests"] == []


async def test_T_H03b_ignore(fx, app):
    async with _two_users(fx, app) as (ca, cb, a_id, b_id):
        req = await _request(ca, b_id)
        r = await cb.post(
            f"/api/v1/web/friend-requests/{req['id']}",
            json={"action": "ignore"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ignored"
        assert await _friend_names(cb) == set()
        # 忽略后可以重新发
        req2 = await _request(ca, b_id)
        assert req2["status"] == "pending"


async def test_T_H04_remove_friend(fx, app):
    async with _two_users(fx, app) as (ca, cb, a_id, b_id):
        await _befriend(ca, cb, a_id, b_id)
        r = await ca.delete(f"/api/v1/web/friends/{b_id}")
        assert r.status_code == 200
        assert await _friend_names(ca) == set()
        assert await _friend_names(cb) == set()
        r = await ca.delete(f"/api/v1/web/friends/{b_id}")
        assert r.status_code == 404


async def test_T_H05_no_auto_friends_and_agent_list(fx, app):
    owner = await fx.make_owner("alice")
    human_id = (
        await fx.db.execute(
            text("SELECT public_id FROM martin.members WHERE owner_id=:o AND human=true"),
            {"o": owner.id},
        )
    ).scalar_one()
    a = await _agent(fx, "H05-A", "Auto A", owner.id)
    b = await _agent(fx, "H05-B", "Auto B", owner.id)

    # 配对不再产生任何好友关系
    assert (await fx.client.get("/api/v1/friends", headers=a["headers"])).json()["friends"] == []
    rows = (
        await fx.db.execute(
            text("SELECT count(*) FROM martin.friendships WHERE member_a=:x OR member_b=:x"),
            {"x": await fx.mid(a["member"]["id"])},
        )
    ).scalar()
    assert rows == 0

    async with web_sessions(app, "alice") as (ca,):
        assert await _friend_names(ca) == set()
        r = await ca.get("/api/v1/web/agents")
        assert r.status_code == 200, r.text
        agents = {x["name"]: x for x in r.json()["agents"]}
        assert set(agents) == {"Auto A", "Auto B"}
        assert agents["Auto A"]["human"] is False
        assert agents["Auto A"]["groups"] == []
        assert agents["Auto A"]["tokenRevoked"] is False

        # 自己的 agent 可以直接私聊，不需要好友关系
        r = await ca.post("/api/v1/web/chats", json={"with": a["member"]["id"]})
        assert r.status_code == 200, r.text
        assert r.json()["group"]["kind"] == "direct"
        gid = r.json()["group"]["id"]
        chats = (await ca.get("/api/v1/web/chats")).json()["chats"]
        assert any(c["id"] == gid and c["peer"]["name"] == "Auto A" for c in chats)


async def test_T_H06_user_direct_chat_pipeline(fx, app):
    async with _two_users(fx, app) as (ca, cb, a_id, b_id):
        await _befriend(ca, cb, a_id, b_id)
        r = await ca.post("/api/v1/web/chats", json={"with": b_id})
        assert r.status_code == 200, r.text
        gid = r.json()["group"]["id"]
        assert r.json()["group"]["kind"] == "direct"
        assert r.json()["peer"]["name"] == "bob"

        # 复用同一个会话
        r = await cb.post("/api/v1/web/chats", json={"with": a_id})
        assert r.json()["group"]["id"] == gid

        r = await ca.post(
            f"/api/v1/web/groups/{gid}/messages", json={"text": "私聊你好"}
        )
        assert r.status_code == 200, r.text
        msgs = (await cb.get(f"/api/v1/web/groups/{gid}/messages")).json()["messages"]
        assert any(m["text"] == "私聊你好" and m["author"]["name"] == "alice" for m in msgs)
        chats = (await cb.get("/api/v1/web/chats")).json()["chats"]
        assert any(c["id"] == gid and c["peer"]["name"] == "alice" for c in chats)


async def test_T_H06b_cross_user_agent_chat_forbidden(fx, app):
    owner_a = await fx.make_owner("alice")
    owner_b = await fx.make_owner("bob")
    a = await _agent(fx, "H06B-A", "Alice Bot", owner_a.id)
    b = await _agent(fx, "H06B-B", "Bob Bot", owner_b.id)

    # agent ↔ 另一个用户的 agent
    r = await fx.client.post(
        "/api/v1/chats", json={"with": b["member"]["id"]}, headers=a["headers"]
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "chat_not_allowed"

    # agent ↔ 另一个用户
    async with web_sessions(app, "bob") as (cb,):
        bob_human_id = (await cb.get("/api/v1/web/me")).json()["humanMember"]["id"]
    r = await fx.client.post(
        "/api/v1/chats", json={"with": bob_human_id}, headers=a["headers"]
    )
    assert r.status_code == 403

    # 用户 ↔ 别人的 agent
    async with web_sessions(app, "alice") as (ca,):
        r = await ca.post("/api/v1/web/chats", json={"with": b["member"]["id"]})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "chat_not_allowed"


async def test_T_H06c_same_owner_chat_without_friendship(fx):
    owner = await fx.make_owner("changyu")
    a = await _agent(fx, "H06C-A", "Same A", owner.id)
    b = await _agent(fx, "H06C-B", "Same B", owner.id)
    r = await fx.client.post(
        "/api/v1/chats", json={"with": b["member"]["id"]}, headers=a["headers"]
    )
    assert r.status_code == 200
    assert r.json()["group"]["kind"] == "direct"


async def test_T_H07_request_state_machine(fx, app):
    async with _two_users(fx, app) as (ca, cb, a_id, b_id):
        # 不能加自己
        r = await ca.post("/api/v1/web/friends", json={"to": a_id, "note": ""})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "self_friend"
        # 目标不存在
        r = await ca.post("/api/v1/web/friends", json={"to": "u00000000", "note": ""})
        assert r.status_code == 404
        # 重复待处理（同向 + 反向）
        req = await _request(ca, b_id)
        r = await ca.post("/api/v1/web/friends", json={"to": b_id, "note": ""})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "request_pending"
        r = await cb.post("/api/v1/web/friends", json={"to": a_id, "note": ""})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "request_pending"
        # 接受后不能重复处理
        await cb.post(
            f"/api/v1/web/friend-requests/{req['id']}", json={"action": "accept"}
        )
        r = await cb.post(
            f"/api/v1/web/friend-requests/{req['id']}", json={"action": "accept"}
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "request_resolved"
        # 已是好友
        r = await ca.post("/api/v1/web/friends", json={"to": b_id, "note": ""})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "already_friends"


async def test_T_H08_direct_group_constraints(fx, app):
    async with _two_users(fx, app) as (ca, cb, a_id, b_id):
        await _befriend(ca, cb, a_id, b_id)
        r = await ca.post("/api/v1/web/chats", json={"with": b_id})
        gid = r.json()["group"]["id"]
        code = (
            await fx.db.execute(
                text("SELECT invite_code FROM martin.groups WHERE id=:g"),
                {"g": await fx.gid(gid)},
            )
        ).scalar_one()

        c = await fx.new_member("H08-C", "Gamma Bot")
        # 私聊不能被加入
        r = await fx.client.post(f"/api/v1/groups/{gid}/join", headers=c["headers"])
        assert r.status_code == 403
        r = await fx.client.post(
            "/api/v1/join", json={"invite": f"/join/{code}"}, headers=c["headers"]
        )
        assert r.status_code == 404
        # 私聊里不能拉 agent
        r = await ca.post(
            f"/api/v1/web/groups/{gid}/agents", json={"agentId": c["member"]["id"]}
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_group"
        # 人类之间没有 token，用 Web 的结束会话接口；双方列表都不再显示
        r = await cb.delete(f"/api/v1/web/chats/{gid}")
        assert r.status_code == 200, r.text
        assert r.json()["dissolved"] is True
        assert all(c["id"] != gid for c in (await ca.get("/api/v1/web/chats")).json()["chats"])


async def test_T_H09_isolation_between_two_owners(fx, app):
    owner_a = await fx.make_owner("alice")
    owner_b = await fx.make_owner("bob")
    await _agent(fx, "H09-A", "Alice Bot", owner_a.id)
    b = await _agent(fx, "H09-B", "Bob Bot", owner_b.id)

    async with web_sessions(app, "alice", "bob") as (ca, cb):
        alice_id = (await ca.get("/api/v1/web/me")).json()["humanMember"]["id"]
        bob_id = (await cb.get("/api/v1/web/me")).json()["humanMember"]["id"]

        # 好友后人类之间可以私聊
        await _befriend(ca, cb, alice_id, bob_id)
        r = await ca.post("/api/v1/web/chats", json={"with": bob_id})
        assert r.status_code == 200, r.text

        # 但好友关系不延伸到 bob 的 agent
        r = await ca.post("/api/v1/web/chats", json={"with": b["member"]["id"]})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "chat_not_allowed"
