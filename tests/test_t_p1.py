"""P1 增量：token 前缀、触发范围、运行状态、question 消息。"""
import pytest

pytestmark = pytest.mark.asyncio


async def _setup(fx, names=("P Bot A", "P Bot B"), group_name="p1-batch"):
    a = await fx.new_member("P1-SEED-A", names[0])
    b = await fx.new_member("P1-SEED-B", names[1])
    g = await fx.create_group(a, group_name)
    r = await fx.client.post(
        "/api/v1/join", json={"invite": g["inviteLink"]}, headers=b["headers"]
    )
    assert r.status_code == 200, r.text
    return a, b, g


# --- token 前缀 --------------------------------------------------------------


async def test_T_T01_me_exposes_token_prefix(fx):
    a, _b, _g = await _setup(fx)
    r = await fx.client.get("/api/v1/me", headers=a["headers"])
    assert r.status_code == 200
    prefix = r.json()["member"].get("tokenPrefix")
    assert prefix and len(prefix) == 10


# --- 触发范围 ----------------------------------------------------------------


async def _wake_rows(fx, member_id, group_id):
    from sqlalchemy import select

    from martin.models import WakeQueue

    member_id = await fx.mid(member_id)
    group_id = await fx.gid(group_id)
    rows = await fx.db.execute(
        select(WakeQueue).where(
            WakeQueue.member_id == member_id, WakeQueue.group_id == group_id
        )
    )
    return rows.scalars().all()


async def test_T_T02_allow_from_owner_only(fx):
    owner = await fx.make_owner("changyu")
    a, _b, g = await _setup(fx)
    # B 归属 owner，且设为仅本人（其 owner 的人类成员）可触发
    await fx.make_code("P1-SEED-E", owner_id=owner.id)
    pair = await fx.client.post(
        "/api/v1/pair",
        json={"code": "P1-SEED-E", "name": "P Bot Own", "platform": "test"},
    )
    assert pair.status_code == 200, pair.text
    b = pair.json()
    b["headers"] = {"Authorization": f"Bearer {b['token']}"}
    join = await fx.client.post(
        "/api/v1/join", json={"invite": g["inviteLink"]}, headers=b["headers"]
    )
    assert join.status_code == 200, join.text
    r = await fx.client.put(
        "/api/v1/wake",
        json={"kind": "listener", "allowFrom": "owner"},
        headers=b["headers"],
    )
    assert r.status_code == 200, r.text
    assert r.json()["wake"]["allowFrom"] == "owner"

    # 其他 agent @B：不唤醒
    await fx.send(a, g["id"], f"@{b['member']['name']} 帮忙", opKey="t02-1")
    assert await _wake_rows(fx, b["member"]["id"], g["id"]) == []

    # 同 owner 的人类成员经邀请链接入群并发言：owner_post 唤醒 B
    login = await fx.web_login("changyu")
    assert login.status_code == 200, login.text
    join_r = await fx.client.post("/api/v1/web/join", json={"invite": g["inviteLink"]})
    assert join_r.status_code == 200, join_r.text
    r = await fx.client.post(
        f"/api/v1/web/groups/{g['id']}/messages",
        json={"text": "大家看一下"},
    )
    assert r.status_code == 200, r.text
    rows = await _wake_rows(fx, b["member"]["id"], g["id"])
    assert len(rows) == 1 and rows[0].reason == "owner_post"


async def test_T_T03_allow_from_list(fx):
    a, b, g = await _setup(fx, names=("P Bot A", "Nina"))
    r = await fx.client.put(
        "/api/v1/wake",
        json={"kind": "listener", "allowFrom": "list", "allowList": ["P Bot A"]},
        headers=b["headers"],
    )
    assert r.status_code == 200
    assert r.json()["wake"]["allowList"] == ["P Bot A"]

    await fx.send(a, g["id"], f"@{b['member']['name']} 在吗", opKey="t03-1")
    rows = await _wake_rows(fx, b["member"]["id"], g["id"])
    assert len(rows) == 1 and rows[0].reason == "mention"

    # 名单外成员 @B：不唤醒
    c = await fx.new_member("P1-SEED-D", "Out Sider")
    join = await fx.client.post(
        "/api/v1/join", json={"invite": g["inviteLink"]}, headers=c["headers"]
    )
    assert join.status_code == 200
    await fx.send(c, g["id"], f"@{b['member']['name']} 也在吗", opKey="t03-2")
    rows = await _wake_rows(fx, b["member"]["id"], g["id"])
    assert len(rows) == 1


# --- 运行状态 ----------------------------------------------------------------


async def test_T_T04_wake_status_endpoint(fx, app):
    a, b, g = await _setup(fx)
    # 免打扰必产生 suppressed 事件，便于断言
    r = await fx.client.put(
        "/api/v1/wake",
        json={"kind": "listener", "muted": True},
        headers=b["headers"],
    )
    assert r.status_code == 200
    await fx.send(a, g["id"], f"@{b['member']['name']} ping", opKey="t04-1")

    r = await fx.client.get("/api/v1/wake/status", headers=b["headers"])
    assert r.status_code == 200
    data = r.json()
    assert data["schema"] == 1
    assert len(data["queue"]) == 1 and data["queue"][0]["status"] == "pending"
    assert data["events"] == []

    await app.state.dispatcher.run_once()
    r = await fx.client.get("/api/v1/wake/status", headers=b["headers"])
    data = r.json()
    assert data["queue"][0]["status"] == "suppressed"
    assert data["events"] and data["events"][0]["result"] == "suppressed"


# --- question 消息 ------------------------------------------------------------


async def test_T_T05_question_roundtrip(fx):
    a, b, g = await _setup(fx)
    r = await fx.client.post(
        f"/api/v1/groups/{g['id']}/messages",
        json={
            "text": "占位正文由服务端渲染",
            "mentions": [b["member"]["name"]],
            "opKey": "t05-1",
            "question": {
                "prompt": "用哪种发布方式？",
                "options": [
                    {"label": "灰度", "consequence": "风险低但慢"},
                    {"label": "全量", "consequence": "快但风险高"},
                ],
                "recommendedIndex": 0,
                "recommendedReason": "首次发布求稳",
            },
        },
        headers=a["headers"],
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["seq"] > 0

    rd = await fx.read(b, g["id"])
    msg = rd.json()["messages"][0]
    assert msg["type"] == "question"
    q = msg["question"]
    assert q["prompt"] == "用哪种发布方式？"
    assert [o["id"] for o in q["options"]] == ["A", "B"]
    assert q["recommended"] == {"id": "A", "reason": "首次发布求稳"}
    assert "建议选 A" in msg["text"] and "A. 灰度" in msg["text"]


async def test_T_T05b_question_validation(fx):
    a, b, g = await _setup(fx)
    bad = await fx.client.post(
        f"/api/v1/groups/{g['id']}/messages",
        json={
            "text": "x",
            "question": {
                "prompt": "p",
                "options": [{"label": "A"}],
                "recommendedIndex": 0,
            },
        },
        headers=a["headers"],
    )
    assert bad.status_code == 422  # options 少于 2，pydantic 拒绝

    bad2 = await fx.client.post(
        f"/api/v1/groups/{g['id']}/messages",
        json={
            "text": "x",
            "question": {
                "prompt": "p",
                "options": [{"label": "A"}, {"label": "B"}],
                "recommendedIndex": 5,
            },
        },
        headers=a["headers"],
    )
    # 越界由 schema 拦截（le=3）
    assert bad2.status_code == 422


async def test_T_T06_question_does_not_block_sending(fx):
    a, b, g = await _setup(fx)
    await fx.client.post(
        f"/api/v1/groups/{g['id']}/messages",
        json={
            "text": "x",
            "question": {"prompt": "p", "options": [{"label": "A"}, {"label": "B"}]},
        },
        headers=a["headers"],
    )
    # B 有未读 question；question 不属于 text，不触发"先读后发"门禁
    r = await fx.send(b, g["id"], "回复问题前先发言", opKey="t06-1")
    assert r.status_code == 200, r.text
