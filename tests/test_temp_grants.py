"""临时授权（temp grant）：一次性码、限时、限条、访客隔离与 owner 回看。"""
import datetime as dt
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from martin.models import Group, Message, TempGrant, WakeConfig, WakeQueue

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def guest_client(app):
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        yield client
    finally:
        await client.aclose()


async def _setup(fx, *, access=30, max_access=None, max_messages=5, allow_from=None, username="alice"):
    owner = await fx.make_owner(username)
    agent = await fx.new_member(f"TMP-{username}", "Bot", owner_id=owner.id)
    if allow_from is not None:
        fx.db.add(
            WakeConfig(
                member_id=await fx.mid(agent["member"]["id"]),
                kind="listener",
                allow_from=allow_from,
                enabled=True,
            )
        )
        await fx.db.commit()
    await fx.web_login(username)
    if max_access is None:
        max_access = access + 60
    r = await fx.client.post(
        f"/api/v1/web/agents/{agent['member']['id']}/temp-grants",
        json={
            "accessMinutes": access,
            "maxAccessMinutes": max_access,
            "maxMessages": max_messages,
            "label": "朋友A",
        },
    )
    assert r.status_code == 200, r.text
    return owner, agent, r.json()["grant"]


async def _redeem(app, session_id, code, label=None):
    async with guest_client(app) as c:
        r = await c.post(
            "/api/v1/temp/redeem",
            json={
                "sessionId": session_id,
                "code": code,
                **({"label": label} if label else {}),
            },
        )
    return r


async def _guest_headers(r):
    token = r.json()["sessionToken"]
    return {"Authorization": f"Bearer {token}"}


async def _guest_send(app, headers, text, op_key=None):
    async with guest_client(app) as c:
        return await c.post(
            "/api/v1/temp/messages",
            json={"text": text, **({"opKey": op_key} if op_key else {})},
            headers=headers,
        )


async def _guest_read(app, headers, after=0):
    async with guest_client(app) as c:
        return await c.get(
            "/api/v1/temp/messages", params={"after": after}, headers=headers
        )


async def test_T_TMP01_create_redeem_guest_chat(fx, app):
    owner, agent, grant = await _setup(fx, max_messages=5)
    assert grant["code"].startswith("TMP-")
    assert len(grant["code"].replace("-", "")) == 11  # TMP + 8

    # 库里只有 hash
    admin = await fx.db.execute(select(TempGrant))
    row = admin.scalar_one()
    assert row.code_hash != grant["code"]

    r = await _redeem(app, grant["id"], grant["code"].lower().replace("-", " "), "小王")
    assert r.status_code == 200, r.text
    assert "set-cookie" not in {k.lower() for k in r.headers}
    headers = await _guest_headers(r)
    group_id = r.json()["temp"]["groupId"]
    assert r.json()["temp"]["agent"]["name"] == "Bot"

    sent = await _guest_send(app, headers, "你好，帮我看看这个")
    assert sent.status_code == 200, sent.text
    assert sent.json()["remainingMessages"] == 4

    # agent 收件箱出现 temp 群
    inbox = await fx.client.get("/api/v1/inbox", headers=agent["headers"])
    assert inbox.status_code == 200, inbox.text
    groups = {g["groupId"]: g for g in inbox.json()["groups"]}
    assert group_id in groups
    assert groups[group_id]["kind"] == "temp"

    # agent 正常读取并回复
    read = await fx.client.get(
        f"/api/v1/groups/{group_id}/messages", headers=agent["headers"]
    )
    assert read.status_code == 200, read.text
    msgs = read.json()["messages"]
    guest_msg = next(m for m in msgs if m["author"]["guest"] and m["type"] == "text")
    assert guest_msg["text"] == "你好，帮我看看这个"
    assert guest_msg["author"]["label"] == "guest"

    reply = await fx.client.post(
        f"/api/v1/groups/{group_id}/messages",
        json={"text": "收到，我看一下"},
        headers=agent["headers"],
    )
    assert reply.status_code == 200, reply.text

    seen = await _guest_read(app, headers)
    assert seen.status_code == 200, seen.text
    assert any(m["text"] == "收到，我看一下" for m in seen.json()["messages"])


async def test_T_TMP02_code_single_use_and_errors(fx, app):
    _owner, _agent, grant = await _setup(fx)
    r1 = await _redeem(app, grant["id"], grant["code"])
    assert r1.status_code == 200
    r2 = await _redeem(app, grant["id"], grant["code"])
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "already_redeemed"

    r3 = await _redeem(app, grant["id"], "TMP-XXXX-XXXX")
    assert r3.status_code == 404
    assert r3.json()["error"]["code"] == "invalid_code"


async def test_T_TMP03_quota_and_read_only(fx, app):
    _owner, _agent, grant = await _setup(fx, max_messages=2)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)

    assert (await _guest_send(app, headers, "1")).status_code == 200
    assert (await _guest_send(app, headers, "2")).status_code == 200
    third = await _guest_send(app, headers, "3")
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "quota_exhausted"

    # 只读仍可用
    read = await _guest_read(app, headers)
    assert read.status_code == 200
    assert len(read.json()["messages"]) >= 3  # system + 2 条


async def test_T_TMP04_revoke_kills_session_and_group(fx, app):
    _owner, agent, grant = await _setup(fx)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)
    group_id = r.json()["temp"]["groupId"]
    await _guest_send(app, headers, "hi")

    rev = await fx.client.delete(f"/api/v1/web/temp-grants/{grant['id']}")
    assert rev.status_code == 200, rev.text
    assert rev.json()["status"] == "revoked"

    async with guest_client(app) as c:
        session = await c.get("/api/v1/temp/session", headers=headers)
    assert session.status_code in (401, 410)

    async with guest_client(app) as c:
        read = await c.get(f"/api/v1/groups/{group_id}/messages", headers=headers)
    assert read.status_code == 401

    detail = await fx.client.get(f"/api/v1/web/temp-grants/{grant['id']}")
    assert detail.json()["grant"]["status"] == "revoked"


async def test_T_TMP05_guest_token_cannot_touch_other_surfaces(fx, app):
    _owner, _agent, grant = await _setup(fx)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)
    async with guest_client(app) as c:
        assert (await c.get("/api/v1/web/me", headers=headers)).status_code == 401
        assert (await c.get("/api/v1/inbox", headers=headers)).status_code == 401
        assert (await c.get("/api/v1/groups", headers=headers)).status_code in (401, 404)
        assert (await c.get("/api/v1/temp/session", headers=headers)).status_code == 200

    # 另一个 owner 读不到
    bob = await fx.make_owner("bob")
    await fx.web_login("bob")
    r = await fx.client.get(f"/api/v1/web/temp-grants/{grant['id']}")
    assert r.status_code == 404


async def test_T_TMP06_wake_reason_and_allow_from_owner(fx, app):
    _owner, agent, grant = await _setup(fx, allow_from="owner")
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)
    sent = await _guest_send(app, headers, "在吗")
    assert sent.status_code == 200, sent.text

    rows = (
        await fx.db.execute(
            select(WakeQueue).where(WakeQueue.member_id == await fx.mid(agent["member"]["id"]))
        )
    ).scalars().all()
    assert rows
    assert rows[-1].reason == "guest_post"


async def test_T_TMP07_temp_group_is_immutable(fx, app):
    _owner, agent, grant = await _setup(fx)
    r = await _redeem(app, grant["id"], grant["code"])
    group_id = r.json()["temp"]["groupId"]

    leave = await fx.client.post(
        f"/api/v1/groups/{group_id}/leave", headers=agent["headers"]
    )
    assert leave.status_code == 403
    assert leave.json()["error"]["code"] == "temp_no_group_ops"

    invite = await fx.client.post(
        f"/api/v1/groups/{group_id}/invite", headers=agent["headers"]
    )
    assert invite.status_code == 403


async def test_T_TMP08_owner_transcript_and_guest_flag(fx, app):
    _owner, _agent, grant = await _setup(fx)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)
    await _guest_send(app, headers, "只读回看内容")

    tr = await fx.client.get(f"/api/v1/web/temp-grants/{grant['id']}/messages")
    assert tr.status_code == 200, tr.text
    msgs = tr.json()["messages"]
    guest = next(m for m in msgs if m["author"]["guest"] and m["type"] == "text")
    assert guest["text"] == "只读回看内容"


async def test_T_TMP09_expiry_blocks_send_and_agent(fx, app):
    _owner, agent, grant = await _setup(fx, max_messages=5)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)
    group_id = r.json()["temp"]["groupId"]

    await fx.db.execute(
        update(TempGrant)
        .where(TempGrant.id == (await fx.db.execute(select(TempGrant.id))).scalar_one())
        .values(expires_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=600))
    )
    await fx.db.commit()

    async with guest_client(app) as c:
        sent = await c.post(
            "/api/v1/temp/messages", json={"text": "too late"}, headers=headers
        )
    assert sent.status_code == 410

    reply = await fx.client.post(
        f"/api/v1/groups/{group_id}/messages",
        json={"text": "agent late reply"},
        headers=agent["headers"],
    )
    assert reply.status_code in (403, 404)


async def test_T_TMP10_at_and_redaction(fx, app):
    _owner, agent, grant = await _setup(fx)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)
    group_id = r.json()["temp"]["groupId"]

    # 访客正文里的 @ 不触发 mention
    sent = await _guest_send(app, headers, "@Bot 你好")
    assert sent.status_code == 200
    msgs = (
        await fx.db.execute(select(Message).where(Message.group_id == (await fx.gid(group_id))))
    ).scalars().all()
    guest = next(m for m in msgs if m.type == "text")
    assert guest.mentions == []

    # agent 先读，再回复（发送前有未读门禁）
    await fx.client.get(f"/api/v1/groups/{group_id}/messages", headers=agent["headers"])
    # agent 回复里的 token 被脱敏
    reply = await fx.client.post(
        f"/api/v1/groups/{group_id}/messages",
        json={"text": "token 是 martin_abcdefghijklmnopqrstuvwxyz"},
        headers=agent["headers"],
    )
    assert reply.status_code == 200
    data = reply.json()["message"]
    assert "martin_abcdefghijklmnopqrstuvwxyz" not in (data["text"] or "")
    assert "已脱敏" in (data["text"] or "")


async def test_T_TMP11_expired_code_cannot_redeem(fx, app):
    _owner, _agent, grant = await _setup(fx)
    await fx.db.execute(
        update(TempGrant)
        .where(TempGrant.id == (await fx.db.execute(select(TempGrant.id))).scalar_one())
        .values(expires_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1))
    )
    await fx.db.commit()
    r = await _redeem(app, grant["id"], grant["code"])
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "code_expired"


async def test_T_TMP12_close_and_opkey_idempotent(fx, app):
    _owner, _agent, grant = await _setup(fx, max_messages=3)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)

    first = await _guest_send(app, headers, "同一条", op_key="same-key")
    assert first.status_code == 200, first.text
    second = await _guest_send(app, headers, "同一条", op_key="same-key")
    assert second.status_code == 200, second.text
    assert second.json()["seq"] == first.json()["seq"]
    # 两次请求只消耗一条额度
    assert second.json()["remainingMessages"] == 2

    async with guest_client(app) as c:
        closed = await c.post("/api/v1/temp/close", headers=headers)
    assert closed.status_code == 200, closed.text

    detail = await fx.client.get(f"/api/v1/web/temp-grants/{grant['id']}")
    assert detail.json()["grant"]["status"] == "closed"
    listed = await fx.client.get("/api/v1/web/temp-grants")
    assert listed.status_code == 200
    assert any(g["id"] == grant["id"] for g in listed.json()["grants"])


@pytest.mark.parametrize("app", [{"temp_code_attempt_limit": 2}], indirect=True)
async def test_T_TMP13_attempt_limit(fx, app):
    _owner, _agent, grant = await _setup(fx)
    first = await _redeem(app, grant["id"], "TMP-BAD1-CODE")
    second = await _redeem(app, grant["id"], "TMP-BAD2-CODE")
    assert first.status_code == 404
    assert second.status_code == 404
    third = await _redeem(app, grant["id"], "TMP-BAD3-CODE")
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "too_many_attempts"


@pytest.mark.parametrize("app", [{"temp_grant_max_messages": 5}], indirect=True)
async def test_T_TMP14_create_limits_and_ownership(fx, app):
    owner, agent, _grant = await _setup(fx, max_messages=5)
    too_many = await fx.client.post(
        f"/api/v1/web/agents/{agent['member']['id']}/temp-grants",
        json={"accessMinutes": 30, "maxAccessMinutes": 60, "maxMessages": 10},
    )
    assert too_many.status_code == 400
    assert too_many.json()["error"]["code"] == "temp_limit"

    bob = await fx.make_owner("bob")
    await fx.web_login("bob")
    other = await fx.client.post(
        f"/api/v1/web/agents/{agent['member']['id']}/temp-grants",
        json={"accessMinutes": 30, "maxAccessMinutes": 60, "maxMessages": 2},
    )
    assert other.status_code == 403


async def test_T_TMP15_group_rules_carry_global_policy(fx, app):
    _owner, agent, grant = await _setup(fx)
    r = await _redeem(app, grant["id"], grant["code"])
    group_id = r.json()["temp"]["groupId"]

    info = await fx.client.get(f"/api/v1/groups/{group_id}", headers=agent["headers"])
    assert info.status_code == 200, info.text
    rules = info.json()["group"]["rules"]
    assert "访客不是 owner" in rules
    assert "owner 不能关闭" in rules
    assert "正常回复访客" in rules

    inbox = await fx.client.get("/api/v1/inbox", headers=agent["headers"])
    entry = next(g for g in inbox.json()["groups"] if g["groupId"] == group_id)
    assert "owner 不能关闭" in entry["rules"]


@pytest.mark.parametrize("app", [{"temp_guest_policy": ""}], indirect=True)
async def test_T_TMP16_missing_global_policy_fails_closed(fx, app):
    owner = await fx.make_owner("alice")
    agent = await fx.new_member("TMP-NOPOLICY", "Bot", owner_id=owner.id)
    await fx.web_login("alice")
    r = await fx.client.post(
        f"/api/v1/web/agents/{agent['member']['id']}/temp-grants",
        json={"accessMinutes": 30, "maxAccessMinutes": 60, "maxMessages": 2},
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "temp_policy_missing"


async def test_T_TMP17_unique_session_url_binding(fx, app):
    _owner, agent, g1 = await _setup(fx, username="alice")
    assert f"/t/{g1['id']}" in g1["shareText"]
    r = await fx.client.post(
        f"/api/v1/web/agents/{agent['member']['id']}/temp-grants",
        json={"accessMinutes": 30, "maxAccessMinutes": 60, "maxMessages": 2},
    )
    assert r.status_code == 200, r.text
    g2 = r.json()["grant"]
    assert g1["id"] != g2["id"]
    assert f"/t/{g2['id']}" in g2["shareText"]

    # 用 A 的码去 B 的 URL：拒绝
    wrong = await _redeem(app, g2["id"], g1["code"])
    assert wrong.status_code == 404
    assert wrong.json()["error"]["code"] == "invalid_code"

    # 正确的 URL 才能核销
    right = await _redeem(app, g1["id"], g1["code"])
    assert right.status_code == 200, right.text


async def test_T_TMP18_renew_keeps_history_and_resets_quota(fx, app):
    _owner, _agent, grant = await _setup(fx, access=30, max_access=120, max_messages=3)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)
    group_id = r.json()["temp"]["groupId"]
    assert (await _guest_send(app, headers, "first message")).status_code == 200
    async with guest_client(app) as c:
        assert (await c.post("/api/v1/temp/close", headers=headers)).status_code == 200

    # 旧 token 失效
    async with guest_client(app) as c:
        old = await c.get("/api/v1/temp/session", headers=headers)
    assert old.status_code in (401, 410)

    renewed = await fx.client.post(f"/api/v1/web/temp-grants/{grant['id']}/renew")
    assert renewed.status_code == 200, renewed.text
    data = renewed.json()["grant"]
    assert data["canRenew"] is True
    assert f"/t/{grant['id']}" in data["shareText"]
    assert data["code"] != grant["code"]

    again = await _redeem(app, grant["id"], data["code"])
    assert again.status_code == 200, again.text
    assert again.json()["temp"]["groupId"] == group_id
    assert again.json()["temp"]["remainingMessages"] == 3
    headers2 = await _guest_headers(again)
    seen = await _guest_read(app, headers2)
    assert any(m["text"] == "first message" for m in seen.json()["messages"])


async def test_T_TMP19_expiry_keeps_history_and_renew(fx, app):
    _owner, _agent, grant = await _setup(fx, access=1, max_access=60, max_messages=2)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)
    group_id = r.json()["temp"]["groupId"]
    assert (await _guest_send(app, headers, "before expiry")).status_code == 200

    await fx.db.execute(
        update(TempGrant)
        .where(TempGrant.public_id == grant["id"])
        .values(expires_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1))
    )
    await fx.db.commit()

    late = await _guest_send(app, headers, "too late")
    assert late.status_code == 410

    detail = await fx.client.get(f"/api/v1/web/temp-grants/{grant['id']}")
    assert detail.status_code == 200
    d = detail.json()["grant"]
    assert d["status"] == "expired"
    assert d["canRenew"] is True
    assert d["hasTranscript"] is True

    tr = await fx.client.get(f"/api/v1/web/temp-grants/{grant['id']}/messages")
    assert any(m["text"] == "before expiry" for m in tr.json()["messages"])

    renewed = await fx.client.post(f"/api/v1/web/temp-grants/{grant['id']}/renew")
    assert renewed.status_code == 200, renewed.text
    new_code = renewed.json()["grant"]["code"]
    again = await _redeem(app, grant["id"], new_code)
    assert again.status_code == 200, again.text
    assert again.json()["temp"]["groupId"] == group_id
    seen = await _guest_read(app, await _guest_headers(again))
    assert any(m["text"] == "before expiry" for m in seen.json()["messages"])


async def test_T_TMP20_max_access_validation_and_expiry(fx, app):
    _owner, agent, _grant = await _setup(fx)
    bad = await fx.client.post(
        f"/api/v1/web/agents/{agent['member']['id']}/temp-grants",
        json={"accessMinutes": 30, "maxAccessMinutes": 10, "maxMessages": 2},
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "temp_invalid_access"

    created = await fx.client.post(
        f"/api/v1/web/agents/{agent['member']['id']}/temp-grants",
        json={"accessMinutes": 1, "maxAccessMinutes": 2, "maxMessages": 2},
    )
    g = created.json()["grant"]
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    await fx.db.execute(
        update(TempGrant)
        .where(TempGrant.public_id == g["id"])
        .values(expires_at=past, max_access_at=past)
    )
    await fx.db.commit()

    detail = await fx.client.get(f"/api/v1/web/temp-grants/{g['id']}")
    assert detail.json()["grant"]["canRenew"] is False
    renewed = await fx.client.post(f"/api/v1/web/temp-grants/{g['id']}/renew")
    assert renewed.status_code == 410
    assert renewed.json()["error"]["code"] == "max_access_reached"


async def test_T_TMP21_agent_finishes_reply_after_expiry(fx, app):
    _owner, agent, grant = await _setup(fx, access=1, max_access=60, max_messages=2)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)
    group_id = r.json()["temp"]["groupId"]
    assert (await _guest_send(app, headers, "please answer")).status_code == 200

    # agent 已经读到这条消息（避免发送前的 409 门禁）
    read = await fx.client.get(
        f"/api/v1/groups/{group_id}/messages", headers=agent["headers"]
    )
    assert read.status_code == 200, read.text

    # 活动窗口刚过期：访客不能再发
    await fx.db.execute(
        update(TempGrant)
        .where(TempGrant.public_id == grant["id"])
        .values(
            expires_at=dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(seconds=5)
        )
    )
    await fx.db.commit()
    assert (await _guest_send(app, headers, "too late")).status_code == 410

    # 但 agent 仍可在默认 300 秒宽限期内把这一轮回复写完
    reply = await fx.client.post(
        f"/api/v1/groups/{group_id}/messages",
        json={"text": "final answer"},
        headers=agent["headers"],
    )
    assert reply.status_code == 200, reply.text

    tr = await fx.client.get(f"/api/v1/web/temp-grants/{grant['id']}/messages")
    assert any(m["text"] == "final answer" for m in tr.json()["messages"])


async def test_T_TMP22_deadline_uses_full_window_and_no_utc_wall_clock(fx, app):
    """5 分钟窗口刚打开时不能显示「约剩 4 分钟」，也不能把 UTC 墙钟写死进消息。"""
    _owner, _agent, grant = await _setup(fx, access=5, max_access=6, max_messages=2)
    r = await _redeem(app, grant["id"], grant["code"])
    assert r.status_code == 200, r.text
    state = r.json()["temp"]

    # 剩余时间按真实秒数下发，不再被 floor 少算近一分钟
    assert state["remainingSeconds"] >= 4 * 60 + 50
    assert state["accessRemainingSeconds"] >= 4 * 60 + 50
    assert state["limitReason"] == "access"

    # serverNow 让浏览器校正本机时钟，截止时刻按浏览器本地时区渲染
    server_now = dt.datetime.fromisoformat(state["serverNow"])
    assert server_now.tzinfo is not None
    expires = dt.datetime.fromisoformat(state["expiresAt"])
    assert 290 <= (expires - server_now).total_seconds() <= 300

    group_id = state["groupId"]
    msgs = (
        await fx.db.execute(
            select(Message).where(Message.group_id == await fx.gid(group_id))
        )
    ).scalars().all()
    system = next(m for m in msgs if m.type == "system")
    assert "UTC" not in system.text
    assert "剩余约 5 分钟" in system.text
    assert "6 分钟" in system.text

    headers = await _guest_headers(r)
    async with guest_client(app) as c:
        session = await c.get("/api/v1/temp/session", headers=headers)
    assert session.status_code == 200, session.text
    assert session.json()["temp"]["serverNow"]

    detail = await fx.client.get(f"/api/v1/web/temp-grants/{grant['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["grant"]["serverNow"]


async def test_T_TMP23_naive_timestamps_are_read_in_db_timezone(fx):
    """老库 `timestamp without time zone` 读回来是 naive：按库时区解释，不能按 UTC 硬读。"""
    from martin.models import AwareDateTime
    from martin.timeutil import as_utc

    col = AwareDateTime("Asia/Shanghai")
    aware = col.process_result_value(dt.datetime(2026, 11, 17, 10, 57, 54), None)
    # 10:57:54 是上海本地时间 = 02:57:54 UTC，不能当成 10:57:54 UTC
    assert aware == dt.datetime(2026, 11, 17, 2, 57, 54, tzinfo=dt.timezone.utc)
    assert col.process_result_value(None, None) is None
    # 已经是 aware 的归一到 UTC（上海 18:57 = 10:57 UTC）
    shanghai = dt.timezone(dt.timedelta(hours=8))
    assert col.process_result_value(
        dt.datetime(2026, 11, 17, 18, 57, 54, tzinfo=shanghai), None
    ) == dt.datetime(2026, 11, 17, 10, 57, 54, tzinfo=dt.timezone.utc)
    # 比较前的兜底：naive 值按 UTC 解释，不会和 aware 的 now 直接比而抛 TypeError
    assert as_utc(dt.datetime(2026, 11, 17, 10, 57, 54)) == dt.datetime(
        2026, 11, 17, 10, 57, 54, tzinfo=dt.timezone.utc
    )


async def test_T_TMP23b_naive_grant_times_do_not_expire_early(fx, app):
    """即使授权行里是 naive 时间，只要还没到，就必须判成 active、能发消息。"""
    from martin.services import temp_grant_status

    _owner, _agent, grant = await _setup(fx, access=5, max_access=60, max_messages=2)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)
    row = (await fx.db.execute(select(TempGrant))).scalar_one()
    # 模拟 naive 读值：同一时刻去掉 tzinfo
    row.expires_at = row.expires_at.replace(tzinfo=None)
    row.max_access_at = row.max_access_at.replace(tzinfo=None)
    assert temp_grant_status(row) == "active"
    assert (await _guest_send(app, headers, "still here")).status_code == 200


async def test_T_TMP24_redeem_starts_full_window(fx, app):
    """码快过期时才核销：访客仍拿到完整访问窗口，上限是最大访问时间。"""
    _owner, _agent, grant = await _setup(fx, access=5, max_access=60, max_messages=2)
    soon = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=40)
    await fx.db.execute(
        update(TempGrant).where(TempGrant.public_id == grant["id"]).values(expires_at=soon)
    )
    await fx.db.commit()

    r = await _redeem(app, grant["id"], grant["code"])
    assert r.status_code == 200, r.text
    state = r.json()["temp"]
    assert state["remainingSeconds"] >= 4 * 60 + 50
    assert state["limitReason"] == "access"


async def test_T_TMP25_agent_reply_during_renew_window(fx, app):
    """续期后、访客核销新码前：agent 的在途回复要落库，不能触发拆群。"""
    _owner, agent, grant = await _setup(fx, access=1, max_access=60, max_messages=2)
    r = await _redeem(app, grant["id"], grant["code"])
    headers = await _guest_headers(r)
    group_id = r.json()["temp"]["groupId"]
    assert (await _guest_send(app, headers, "question")).status_code == 200
    read = await fx.client.get(
        f"/api/v1/groups/{group_id}/messages", headers=agent["headers"]
    )
    assert read.status_code == 200, read.text

    # 活动窗口到期，owner 续期；此刻访客还没核销新码（redeemed_at 为空）
    await fx.db.execute(
        update(TempGrant)
        .where(TempGrant.public_id == grant["id"])
        .values(
            expires_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
        )
    )
    await fx.db.commit()
    renewed = await fx.client.post(f"/api/v1/web/temp-grants/{grant['id']}/renew")
    assert renewed.status_code == 200, renewed.text
    new_code = renewed.json()["grant"]["code"]

    # agent 的在途回复照常写入，且不能把群拆掉
    reply = await fx.client.post(
        f"/api/v1/groups/{group_id}/messages",
        json={"text": "在途回复"},
        headers=agent["headers"],
    )
    assert reply.status_code == 200, reply.text
    grp = await fx.db.get(Group, await fx.gid(group_id))
    assert grp.dissolved_at is None

    # 访客核销新码重新进入，能看到续期窗口内落库的回复
    again = await _redeem(app, grant["id"], new_code)
    assert again.status_code == 200, again.text
    seen = await _guest_read(app, await _guest_headers(again))
    assert any(m["text"] == "在途回复" for m in seen.json()["messages"])
