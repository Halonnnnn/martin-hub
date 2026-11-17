import asyncio
import datetime as dt

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def _setup(fx, names=("Alice Bot", "Bob Bot")):
    a = await fx.new_member("C-SEED-A", names[0])
    b = await fx.new_member("C-SEED-B", names[1])
    g = await fx.create_group(a, f"chat-{names[0].split()[0].lower()}")
    r = await fx.client.post(
        "/api/v1/join", json={"invite": g["inviteLink"]}, headers=b["headers"]
    )
    assert r.status_code == 200
    return a, b, g


async def test_T_C01_send_and_unread(fx):
    a, b, g = await _setup(fx)
    r = await fx.send(a, g["id"], "hello world")
    assert r.status_code == 200
    assert r.json()["seq"] == 1

    r = await fx.read(b, g["id"])
    msgs = r.json()["messages"]
    assert len(msgs) == 1
    assert msgs[0]["new"] is True
    assert msgs[0]["text"] == "hello world"

    r2 = await fx.read(b, g["id"])
    assert r2.json()["messages"] == []


async def test_T_C02_seq_unique_under_concurrency(fx):
    a, b, g = await _setup(fx)
    rs = await asyncio.gather(*[fx.send(a, g["id"], f"msg {i}") for i in range(5)])
    assert all(r.status_code == 200 for r in rs)
    seqs = sorted(r.json()["seq"] for r in rs)
    assert seqs == [1, 2, 3, 4, 5]


async def test_T_C03_opkey_idempotent(fx):
    a, b, g = await _setup(fx)
    r1 = await fx.send(a, g["id"], "only once", opKey="op-123")
    assert r1.status_code == 200
    r2 = await fx.send(a, g["id"], "only once", opKey="op-123")
    assert r2.status_code == 200
    assert r2.json()["idempotent"] is True
    assert r2.json()["seq"] == r1.json()["seq"]

    r = await fx.read(b, g["id"])
    texts = [m["text"] for m in r.json()["messages"]]
    assert texts.count("only once") == 1


async def test_T_C04_cursor_paging(fx):
    a, b, g = await _setup(fx)
    for i in range(5):
        await fx.send(a, g["id"], f"m{i + 1}")

    r = await fx.read(b, g["id"], after=2, limit=2)
    seqs = [m["seq"] for m in r.json()["messages"]]
    assert seqs == [3, 4]

    r = await fx.read(b, g["id"], before=3, limit=10)
    seqs = [m["seq"] for m in r.json()["messages"]]
    assert seqs == [1, 2]

    r = await fx.read(b, g["id"], before=1)
    assert r.json()["messages"] == []


async def test_T_C05_mention_parse_and_hint(fx):
    a, b, g = await _setup(fx, names=("Alice Bot", "Bobby"))
    r = await fx.send(a, g["id"], "@Bobby 看一下这个")
    data = r.json()
    assert "Bobby" in data["mentioned"]
    assert data["mentionHint"] is None

    r = await fx.send(a, g["id"], "@Nobody 帮个忙")
    data = r.json()
    assert data["mentioned"] == []
    assert "Nobody" in data["mentionHint"]

    r = await fx.client.post(
        f"/api/v1/groups/{g['id']}/messages",
        json={"text": "数组提及", "mentions": ["Bobby"]},
        headers=a["headers"],
    )
    assert "Bobby" in r.json()["mentioned"]


async def test_T_C06_all_agents_owner_human_only(fx):
    owner = await fx.make_owner("changyu")
    a = await fx.new_member("C06-CODA", "Worker Bot", owner_id=owner.id)
    g = await fx.create_group(a, "allagents")

    # 无会话（未登录）发 all agents → 401
    r = await fx.client.post(
        f"/api/v1/groups/{g['id']}/messages",
        json={"text": "大家注意", "mentions": ["all agents"]},
    )
    assert r.status_code == 401

    # agent 发 all agents → 403
    r = await fx.send(a, g["id"], "集合", mentions=["all agents"])
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


async def test_T_C06b_all_agents_by_owner_ok(fx):
    owner = await fx.make_owner("changyu")
    a = await fx.new_member("C6B-CODA", "Worker Bot", owner_id=owner.id)
    g = await fx.create_group(a, "allagents-ok")
    login = await fx.web_login("changyu")
    assert login.status_code == 200
    join = await fx.client.post(f"/api/v1/web/groups/{g['id']}/join")
    assert join.status_code == 200
    r = await fx.client.post(
        f"/api/v1/web/groups/{g['id']}/messages",
        json={"text": "大家注意", "mentions": ["all agents"]},
    )
    assert r.status_code == 200
    assert "all agents" in r.json()["mentioned"]


async def test_T_C07_reply_validation(fx):
    a, b, g = await _setup(fx)
    r = await fx.send(a, g["id"], "bad reply", replyTo=99)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_reply"

    r = await fx.send(a, g["id"], "m1")
    await fx.read(b, g["id"])
    r = await fx.send(b, g["id"], "replying", replyTo=1)
    assert r.status_code == 200
    assert r.json()["message"]["replyToSeq"] == 1


async def test_T_C08_new_messages_409(fx):
    a, b, g = await _setup(fx)
    await fx.read(a, g["id"])
    await fx.send(b, g["id"], "B 抢先说了")

    r = await fx.send(a, g["id"], "A 的回复")
    assert r.status_code == 409
    data = r.json()
    assert data["error"]["code"] == "new_messages"
    assert [m["text"] for m in data["messages"]] == ["B 抢先说了"]

    r2 = await fx.send(a, g["id"], "A 的回复")
    assert r2.status_code == 200

    await fx.send(b, g["id"], "B 又说")
    r3 = await fx.send(a, g["id"], "不管了直接发", ignoreNew=True)
    assert r3.status_code == 200


async def test_T_C09_recall_window(fx):
    a, b, g = await _setup(fx)
    r = await fx.send(a, g["id"], "会被撤回")
    seq = r.json()["seq"]

    r = await fx.client.delete(
        f"/api/v1/groups/{g['id']}/messages/{seq}", headers=b["headers"]
    )
    assert r.status_code == 403

    r = await fx.client.delete(
        f"/api/v1/groups/{g['id']}/messages/{seq}", headers=a["headers"]
    )
    assert r.status_code == 200

    r = await fx.read(b, g["id"])
    data = r.json()
    recalled = [m for m in data["messages"] if m["recalled"]]
    assert seq in data["recalled"]
    assert recalled and recalled[0]["text"] is None

    r = await fx.send(a, g["id"], "旧消息占位")
    seq2 = r.json()["seq"]
    await fx.db.execute(
        text("UPDATE martin.messages SET created_at = now() - interval '3 minutes' WHERE seq=:s"),
        {"s": seq2},
    )
    await fx.db.commit()
    r = await fx.client.delete(
        f"/api/v1/groups/{g['id']}/messages/{seq2}", headers=a["headers"]
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "recall_expired"


@pytest.mark.parametrize("app", [{"group_hourly_message_limit": 3}], indirect=True)
async def test_T_C10_rate_limited(fx):
    a, b, g = await _setup(fx)
    results = []
    for i in range(4):
        r = await fx.send(a, g["id"], f"spam {i}")
        results.append(r)
    statuses = [r.status_code for r in results]
    assert statuses.count(429) >= 1
    limited = next(r for r in results if r.status_code == 429)
    assert limited.json()["error"]["code"] == "rate_limited"


async def test_T_C11_text_too_long(fx):
    a, b, g = await _setup(fx)
    r = await fx.send(a, g["id"], "x" * 10001)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "text_too_long"


async def test_T_C12_relay_tell_owner_once(fx):
    a, b, g = await _setup(fx)
    r1 = await fx.send(a, g["id"], "用户让我转达", relay=True)
    assert r1.status_code == 200
    assert r1.json()["tellOwner"]

    r2 = await fx.send(a, g["id"], "用户又让我转达", relay=True)
    assert r2.json()["tellOwner"] is None


async def test_T_C13_backlog_truncation(fx):
    a, b, g = await _setup(fx)
    await fx.send(b, g["id"], "开场白")
    for i in range(30):
        await fx.send(b, g["id"], f"刷屏 {i}" + (" @Alice Bot" if i in (5, 25) else ""))

    r = await fx.read(a, g["id"], limit=10)
    data = r.json()
    assert data["skipped"] > 0
    assert len(data["messages"]) <= 10
    texts = [m["text"] for m in data["messages"]]
    assert any("@Alice Bot" in t for t in texts)
    cursor_row = await fx.db.execute(
        text("SELECT max(seq) FROM martin.messages WHERE group_id=:gid"),
        {"gid": await fx.gid(g["id"])},
    )
    assert data["cursor"] == cursor_row.scalar()

    r2 = await fx.read(a, g["id"])
    assert r2.json()["messages"] == []


async def test_T_C14_system_messages(fx):
    a, b, g = await _setup(fx)

    await fx.client.delete(
        f"/api/v1/groups/{g['id']}/members/{b['member']['id']}", headers=a["headers"]
    )
    r = await fx.read(a, g["id"])
    texts = [m["text"] for m in r.json()["messages"] if m["type"] == "system"]
    assert any("被移出群" in t for t in texts)

    r = await fx.client.delete(f"/api/v1/groups/{g['id']}", headers=a["headers"])
    assert r.status_code == 200

    g2 = await fx.create_group(a, "temp2")
    r = await fx.client.post(
        "/api/v1/join", json={"invite": g2["inviteLink"]}, headers=b["headers"]
    )
    r = await fx.client.post(f"/api/v1/groups/{g2['id']}/leave", json={}, headers=b["headers"])
    assert r.status_code == 200
    r = await fx.read(a, g2["id"])
    texts = [m["text"] for m in r.json()["messages"] if m["type"] == "system"]
    assert any("离开了群" in t for t in texts)
