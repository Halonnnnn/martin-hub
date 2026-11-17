"""V 段：一个群里的多个人类用户，以及用户不可被 @。"""
import datetime as dt
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


async def _wake_rows(fx, member_id: str, group_id: str):
    rows = await fx.db.execute(
        text(
            "SELECT reason, status FROM martin.wake_queue "
            "WHERE member_id=:m AND group_id=:g"
        ),
        {"m": await fx.mid(member_id), "g": await fx.gid(group_id)},
    )
    return rows.all()


@asynccontextmanager
async def _two_humans_in_room(fx, app, name="v-room"):
    """alice 的 agent 建群，alice 按群码入群，bob 按邀请链接入群。"""
    alice = await fx.make_owner("alice")
    bob = await fx.make_owner("bob")
    agent = await fx.new_member("V-A", "Alice Bot", owner_id=alice.id)
    group = await fx.create_group(agent, name, topic="多用户群")
    async with web_sessions(app, "alice", "bob") as (ca, cb):
        assert (
            await ca.post(f"/api/v1/web/groups/{group['id']}/join")
        ).status_code == 200
        joined = await cb.post(
            "/api/v1/web/join", json={"invite": group["inviteLink"]}
        )
        assert joined.status_code == 200, joined.text
        assert joined.json()["joined"] is True
        yield alice, bob, agent, group, ca, cb


async def test_T_V01_two_users_share_group(fx, app):
    async with _two_humans_in_room(fx, app, "two-users") as (
        _alice,
        _bob,
        agent,
        group,
        ca,
        cb,
    ):
        # 两个用户都能在侧栏看到这个群，且都拿到邀请链接
        for client in (ca, cb):
            groups = (await client.get("/api/v1/web/groups")).json()["groups"]
            row = next(g for g in groups if g["id"] == group["id"])
            assert row["inviteLink"].startswith("/join/")

        # 成员列表里两个人类成员与 agent 并存
        members = (
            await ca.get(f"/api/v1/web/groups/{group['id']}/members")
        ).json()["members"]
        by_name = {m["name"]: m for m in members}
        assert by_name["alice"]["human"] is True
        assert by_name["bob"]["human"] is True
        assert by_name["Alice Bot"]["human"] is False
        assert len(members) == 3

        # alice 发言，bob 能看到，且不会把 alice 认成自己的 owner
        sent = await ca.post(
            f"/api/v1/web/groups/{group['id']}/messages",
            json={"text": "bob 你好"},
        )
        assert sent.status_code == 200, sent.text
        msgs = (
            await cb.get(f"/api/v1/web/groups/{group['id']}/messages")
        ).json()["messages"]
        first = msgs[-1]
        assert first["author"]["name"] == "alice"
        assert first["author"]["human"] is True
        assert first["author"]["yourOwner"] is False

        # bob 回复，alice 也能看到
        assert (
            await cb.post(
                f"/api/v1/web/groups/{group['id']}/messages",
                json={"text": "收到"},
            )
        ).status_code == 200
        msgs = (
            await ca.get(f"/api/v1/web/groups/{group['id']}/messages")
        ).json()["messages"]
        assert msgs[-1]["author"]["name"] == "bob"
        assert msgs[-1]["author"]["human"] is True

        # agent 的成员列表也一致
        api_members = (
            await fx.client.get(
                f"/api/v1/groups/{group['id']}/members", headers=agent["headers"]
            )
        ).json()["members"]
        assert {"alice", "bob", "Alice Bot"} <= {m["name"] for m in api_members}


async def test_T_V02_humans_cannot_be_mentioned(fx, app):
    async with _two_humans_in_room(fx, app, "no-at-users") as (
        _alice,
        bob,
        _agent,
        group,
        ca,
        cb,
    ):
        # 用户之间也不能 @：正文、mentions 字段两条路径都忽略，并给出明确提示
        r = await ca.post(
            f"/api/v1/web/groups/{group['id']}/messages",
            json={"text": "@bob 看一下", "mentions": ["bob"]},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["mentioned"] == []
        assert "是用户，不能被 @" in data["mentionHint"]

        r = await ca.post(
            f"/api/v1/web/groups/{group['id']}/messages",
            json={"text": "再看一眼", "mentions": ["@bob"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["mentioned"] == []
        assert "是用户，不能被 @" in r.json()["mentionHint"]

        stored = await fx.db.execute(
            text(
                "SELECT mentions FROM martin.messages "
                "WHERE group_id=:g ORDER BY seq DESC LIMIT 1"
            ),
            {"g": await fx.gid(group["id"])},
        )
        assert stored.scalar_one() == []

        # 同群里 @agent 仍然正常；bob 的 agent 入群后可以按精确名字被 @
        b = await fx.new_member("V-B", "Bob Bot", owner_id=bob.id)
        assert (
            await fx.client.post(
                "/api/v1/join", json={"invite": group["inviteLink"]}, headers=b["headers"]
            )
        ).status_code == 200
        r = await fx.send(b, group["id"], "@Alice Bot 请复核", ignoreNew=True)
        assert r.status_code == 200, r.text
        assert r.json()["mentioned"] == ["Alice Bot"]
        assert r.json()["mentionHint"] is None


async def test_T_V03_inbox_owner_post_is_scoped_to_own_owner(fx, app):
    async with _two_humans_in_room(fx, app, "owner-scope") as (
        _alice,
        _bob,
        agent,
        group,
        ca,
        cb,
    ):
        # bob 先发言：对 alice 的 agent 来说不是 fromYourOwner
        assert (
            await cb.post(
                f"/api/v1/web/groups/{group['id']}/messages", json={"text": "我是 bob"}
            )
        ).status_code == 200
        inbox = (await fx.client.get("/api/v1/inbox", headers=agent["headers"])).json()
        row = next(g for g in inbox["groups"] if g["groupId"] == group["id"])
        assert "fromYourOwner" not in row
        assert row["unread"] == 1

        # alice 发言后只记她这一条
        assert (
            await ca.post(
                f"/api/v1/web/groups/{group['id']}/messages", json={"text": "我是 alice"}
            )
        ).status_code == 200
        inbox = (await fx.client.get("/api/v1/inbox", headers=agent["headers"])).json()
        row = next(g for g in inbox["groups"] if g["groupId"] == group["id"])
        assert row["fromYourOwner"] == 1
        assert row["unread"] == 2


async def test_T_V04_join_by_invite_and_rejoin(fx, app):
    alice = await fx.make_owner("alice")
    bob = await fx.make_owner("bob")
    agent = await fx.new_member("V-D", "Host Bot", owner_id=alice.id)
    group = await fx.create_group(agent, "invite-door")

    # 未登录不能入群
    r = await fx.client.post(
        "/api/v1/web/join", json={"invite": group["inviteLink"]}
    )
    assert r.status_code == 401

    async with web_sessions(app, "bob") as (cb,):
        # 无效链接
        r = await cb.post("/api/v1/web/join", json={"invite": "/join/NOT-A-CODE"})
        assert r.status_code == 404

        # 非群主用户不能凭群 id 入群，必须用邀请链接
        r = await cb.post(f"/api/v1/web/groups/{group['id']}/join")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "forbidden"

        # 带完整域名的邀请链接也能识别
        r = await cb.post(
            "/api/v1/web/join",
            json={"invite": f"http://example.com{group['inviteLink']}"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["joined"] is True
        assert r.json()["group"]["inviteLink"].startswith("/join/")

        # 重复加入是幂等的
        r = await cb.post("/api/v1/web/join", json={"invite": group["inviteLink"]})
        assert r.status_code == 200
        assert r.json()["joined"] is False

        # 被移出后可以凭同一链接重新加入
        bob_human_id = (
            await fx.db.execute(
                text("SELECT id FROM martin.members WHERE name='bob'")
            )
        ).scalar_one()
        await fx.db.execute(
            text(
                "UPDATE martin.group_members SET removed_at=:now "
                "WHERE group_id=:g AND member_id=:m"
            ),
            {
                "now": dt.datetime.now(dt.timezone.utc),
                "g": await fx.gid(group["id"]),
                "m": bob_human_id,
            },
        )
        await fx.db.commit()
        r = await cb.post("/api/v1/web/join", json={"invite": group["inviteLink"]})
        assert r.status_code == 200, r.text
        assert r.json()["joined"] is True

        members = (
            await cb.get(f"/api/v1/web/groups/{group['id']}/members")
        ).json()["members"]
        assert "bob" in {m["name"] for m in members}


async def test_T_V05_wake_scope_between_two_owners(fx, app):
    async with _two_humans_in_room(fx, app, "wake-scope") as (
        _alice,
        bob,
        alice_agent,
        group,
        ca,
        cb,
    ):
        bob_agent = await fx.new_member("V-E", "Bob Helper", owner_id=bob.id)
        assert (
            await fx.client.post(
                "/api/v1/join",
                json={"invite": group["inviteLink"]},
                headers=bob_agent["headers"],
            )
        ).status_code == 200
        # bob 的 agent 只接受自己 owner 触发
        assert (
            await fx.client.put(
                "/api/v1/wake",
                json={"kind": "listener", "allowFrom": "owner"},
                headers=bob_agent["headers"],
            )
        ).status_code == 200

        # alice 发言：不唤醒 bob 的 agent；所有 agent 仍收到 alice 的 owner_post 动作
        assert (
            await ca.post(
                f"/api/v1/web/groups/{group['id']}/messages", json={"text": "alice 在说"}
            )
        ).status_code == 200
        assert await _wake_rows(fx, bob_agent["member"]["id"], group["id"]) == []
        rows = await _wake_rows(fx, alice_agent["member"]["id"], group["id"])
        assert [r[0] for r in rows] == ["owner_post"]

        # bob 发言：唤醒他自己的 agent
        assert (
            await cb.post(
                f"/api/v1/web/groups/{group['id']}/messages", json={"text": "bob 在说"}
            )
        ).status_code == 200
        rows = await _wake_rows(fx, bob_agent["member"]["id"], group["id"])
        assert [r[0] for r in rows] == ["owner_post"]
