"""T-N 紧急推送（05 方案 v0.3）：全部假 agent，不跑真实 Claude Code 等 agent。"""
import asyncio
import json

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


class FakeWebhook:
    """测试内起的假 webhook 接收端，记录请求并可编排响应状态。"""

    def __init__(self):
        self.requests: list[dict] = []
        self.statuses: list[int] = []
        self.on_wake = None
        self._server = None
        self.port = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/hook"

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        lines = head.split("\r\n")
        headers = {}
        for line in lines[1:]:
            if ": " in line:
                k, v = line.split(": ", 1)
                headers[k.lower()] = v
        length = int(headers.get("content-length", 0))
        body = await reader.readexactly(length) if length else b""
        payload = json.loads(body) if body else None
        self.requests.append({"headers": headers, "json": payload})
        if payload and payload.get("kind") == "wake" and self.on_wake is not None:
            await self.on_wake(payload)
        status = self.statuses.pop(0) if self.statuses else 200
        writer.write(
            f"HTTP/1.1 {status} X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        writer.close()

    def wakes(self) -> list[dict]:
        return [r["json"] for r in self.requests if r["json"] and r["json"].get("kind") == "wake"]


@pytest.fixture
async def webhook():
    hook = FakeWebhook()
    await hook.start()
    yield hook
    await hook.stop()


@pytest.fixture
async def live(app):
    """SSE 需要真实 HTTP 流：ASGITransport 会缓冲整个响应，无法消费无限流。"""
    import httpx
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False
    ) as client:
        yield client
    server.should_exit = True
    await task


async def _setup(fx, a_name="Nrg A", b_name="Nrg B", group_name="n-urgent"):
    a = await fx.new_member("N-SEED-A", a_name)
    b = await fx.new_member("N-SEED-B", b_name)
    g = await fx.create_group(a, group_name)
    r = await fx.client.post(
        "/api/v1/join", json={"invite": g["inviteLink"]}, headers=b["headers"]
    )
    assert r.status_code == 200, r.text
    return a, b, g


async def _config_webhook(fx, member, hook, secret="s3cret", muted=None):
    body = {"kind": "webhook", "url": hook.url, "secret": secret}
    if muted is not None:
        body["muted"] = muted
    r = await fx.client.put("/api/v1/wake", json=body, headers=member["headers"])
    assert r.status_code == 200, r.text
    return r.json()


async def _config_listener(fx, member, muted=None):
    body = {"kind": "listener"}
    if muted is not None:
        body["muted"] = muted
    r = await fx.client.put("/api/v1/wake", json=body, headers=member["headers"])
    assert r.status_code == 200, r.text
    return r.json()


async def _run(app):
    return await app.state.dispatcher.run_once()


async def _queue(fx, member_id, group_id):
    member_id = await fx.mid(member_id)
    group_id = await fx.gid(group_id)
    rows = await fx.db.execute(
        text(
            "SELECT id, reason, urgent, status, attempts FROM martin.wake_queue "
            "WHERE member_id=:m AND group_id=:g ORDER BY id"
        ),
        {"m": member_id, "g": group_id},
    )
    return [dict(zip(("id", "reason", "urgent", "status", "attempts"), r)) for r in rows.all()]


async def _read_events(resp, count, timeout=6.0):
    out = []
    cur_id = None

    async def collect():
        nonlocal cur_id
        async for line in resp.aiter_lines():
            if line.startswith("id:"):
                cur_id = int(line[3:].strip())
            elif line.startswith("data:"):
                out.append((cur_id, json.loads(line[5:].strip())))
                if len(out) >= count:
                    return

    await asyncio.wait_for(collect(), timeout)
    return out


async def _read_stream(client, member, want_events=0, want_ping=False, timeout=6.0):
    """单次迭代同时收集事件与心跳（httpx 流不允许重复消费）。"""
    events = []
    ping = False
    cur_id = None

    async def collect(resp):
        nonlocal cur_id, ping
        async for line in resp.aiter_lines():
            if line.startswith("id:"):
                cur_id = int(line[3:].strip())
            elif line.startswith("data:"):
                events.append((cur_id, json.loads(line[5:].strip())))
            elif line.startswith(": ping"):
                ping = True
            if len(events) >= want_events and (ping or not want_ping):
                return

    async with client.stream(
        "GET", "/api/v1/wake/events", headers=member["headers"]
    ) as resp:
        assert resp.status_code == 200
        await asyncio.wait_for(collect(resp), timeout)
    return events, ping


# --- 发送语义 -------------------------------------------------------------


async def test_T_N01_priority_persisted(fx):
    a, b, g = await _setup(fx)
    r = await fx.send(a, g["id"], "普通消息")
    assert r.json()["priority"] == "normal"
    r = await fx.send(a, g["id"], "紧急消息", priority="urgent")
    assert r.status_code == 200, r.text
    assert r.json()["priority"] == "urgent"
    assert r.json()["message"]["priority"] == "urgent"

    msgs = (await fx.read(b, g["id"])).json()["messages"]
    assert [m["priority"] for m in msgs] == ["normal", "urgent"]


async def test_T_N02_invalid_priority(fx):
    a, b, g = await _setup(fx)
    r = await fx.send(a, g["id"], "x", priority="asap")
    assert r.status_code == 422
    row = await fx.db.execute(text("SELECT count(*) FROM martin.messages"))
    assert row.scalar_one() == 0


async def test_T_N03_urgent_enqueue_flag(fx):
    a, b, g = await _setup(fx)
    await fx.send(a, g["id"], f"@{b['member']['name']} 快", priority="urgent")
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert len(rows) == 1
    assert rows[0]["urgent"] is True
    assert rows[0]["reason"] == "mention"
    assert rows[0]["status"] == "pending"


@pytest.mark.parametrize("app", [{"urgent_group_hourly_limit": 1}], indirect=True)
async def test_T_N04_group_urgent_budget(fx, app):
    a, b, g = await _setup(fx)
    name = b["member"]["name"]
    r1 = await fx.send(a, g["id"], f"@{name} u1", priority="urgent")
    assert r1.json()["priority"] == "urgent"
    assert r1.json()["urgentDowngraded"] is None
    r2 = await fx.send(a, g["id"], f"@{name} u2", priority="urgent")
    assert r2.status_code == 200
    assert r2.json()["priority"] == "normal"
    assert r2.json()["urgentDowngraded"] == "urgent_limited"
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert [r["urgent"] for r in rows] == [True, False]


@pytest.mark.parametrize("app", [{"urgent_sender_daily_limit": 1}], indirect=True)
async def test_T_N05_sender_urgent_budget(fx, app):
    a, b, g1 = await _setup(fx, group_name="n-budget-1")
    g2 = await fx.create_group(a, "n-budget-2")
    await fx.client.post(
        "/api/v1/join", json={"invite": g2["inviteLink"]}, headers=b["headers"]
    )
    name = b["member"]["name"]
    r1 = await fx.send(a, g1["id"], f"@{name} u1", priority="urgent")
    r2 = await fx.send(a, g2["id"], f"@{name} u2", priority="urgent")
    assert r1.json()["priority"] == "urgent"
    assert r2.json()["priority"] == "normal"
    assert r2.json()["urgentDowngraded"] == "urgent_limited"


async def test_T_N06_degraded_reasons(fx, webhook):
    a, b, g = await _setup(fx)
    plain = await fx.new_member("N-SEED-C", "Nrg Plain")
    muted = await fx.new_member("N-SEED-D", "Nrg Muted")
    broken = await fx.new_member("N-SEED-E", "Nrg Broken")
    offline = await fx.new_member("N-SEED-F", "Nrg Offline")
    for m in (plain, muted, broken, offline):
        await fx.client.post(
            "/api/v1/join", json={"invite": g["inviteLink"]}, headers=m["headers"]
        )
    await _config_webhook(fx, muted, webhook, muted=True)
    webhook.statuses.append(500)
    r = await _config_webhook(fx, broken, webhook)
    assert r["test"]["ok"] is False and r["wake"]["enabled"] is False
    await _config_listener(fx, offline)

    names = [m["member"]["name"] for m in (plain, muted, broken, offline)]
    r = await fx.send(a, g["id"], "紧急", mentions=names, priority="urgent")
    assert r.status_code == 200, r.text
    degraded = {d["name"]: d["reason"] for d in r.json()["push"]["degraded"]}
    assert degraded == {
        "Nrg Plain": "no_channel",
        "Nrg Muted": "muted",
        "Nrg Broken": "disabled",
        "Nrg Offline": "listener_offline",
    }
    assert r.json()["push"]["agents"] == 0
    assert "免打扰" in r.json()["pushHint"]


async def test_T_N07_inbox_urgent_count(fx):
    a, b, g = await _setup(fx)
    await fx.send(a, g["id"], "u", priority="urgent")
    await fx.send(a, g["id"], "n")
    inbox = (
        await fx.client.get("/api/v1/inbox", headers=b["headers"])
    ).json()
    entry = next(x for x in inbox["groups"] if x["groupId"] == g["id"])
    assert entry["urgent"] == 1 and entry["unread"] == 2
    await fx.read(b, g["id"])
    inbox = (
        await fx.client.get("/api/v1/inbox", headers=b["headers"])
    ).json()
    entry = next(x for x in inbox["groups"] if x["groupId"] == g["id"])
    assert entry["urgent"] == 0


async def test_T_N08_opkey_idempotent_urgent(fx):
    a, b, g = await _setup(fx)
    name = b["member"]["name"]
    r1 = await fx.send(a, g["id"], f"@{name} u", priority="urgent", opKey="n-op-1")
    r2 = await fx.send(a, g["id"], f"@{name} u", priority="urgent", opKey="n-op-1")
    assert r2.json()["idempotent"] is True
    assert r2.json()["seq"] == r1.json()["seq"]
    assert len(await _queue(fx, b["member"]["id"], g["id"])) == 1


async def test_T_N09_urgent_not_bypass_409(fx):
    a, b, g = await _setup(fx)
    await fx.send(b, g["id"], "先有新消息")
    r = await fx.send(a, g["id"], "u", priority="urgent")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "new_messages"


# --- 唤醒配置 -------------------------------------------------------------


async def test_T_N10_wake_crud_and_test(fx, webhook):
    a, b, g = await _setup(fx)
    r = await fx.client.get("/api/v1/wake", headers=b["headers"])
    assert r.json()["wake"] is None

    cfg = await _config_webhook(fx, b, webhook)
    assert cfg["test"]["ok"] is True
    assert cfg["wake"]["kind"] == "webhook" and cfg["wake"]["enabled"] is True
    assert "secret" not in json.dumps(cfg)
    assert webhook.requests[-1]["headers"]["authorization"] == "Bearer s3cret"

    r = await fx.client.put(
        "/api/v1/wake",
        json={"kind": "webhook", "url": "file:///etc/passwd", "secret": "x"},
        headers=b["headers"],
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_wake_url"
    r = await fx.client.put(
        "/api/v1/wake", json={"kind": "webhook", "url": webhook.url}, headers=b["headers"]
    )
    assert r.status_code == 400

    webhook.statuses.append(503)
    r = await fx.client.post("/api/v1/wake/test", headers=b["headers"])
    assert r.json()["test"]["ok"] is False
    assert (await fx.client.get("/api/v1/wake", headers=b["headers"])).json()["wake"]["enabled"] is False

    r = await fx.client.post("/api/v1/wake/test", headers=b["headers"])
    assert r.json()["test"]["ok"] is True

    lst = await _config_listener(fx, b)
    assert lst["wake"]["kind"] == "listener"
    r = await fx.client.post("/api/v1/wake/test", headers=b["headers"])
    assert r.json()["test"]["ok"] is False  # 尚无 SSE 连接

    r = await fx.client.delete("/api/v1/wake", headers=b["headers"])
    assert r.status_code == 200
    assert (await fx.client.get("/api/v1/wake", headers=b["headers"])).json()["wake"] is None


# --- webhook 派发 ---------------------------------------------------------


async def test_T_N11_webhook_dispatch(fx, app, webhook):
    a, b, g = await _setup(fx)
    await _config_webhook(fx, b, webhook)
    webhook.requests.clear()
    await fx.send(a, g["id"], f"@{b['member']['name']} 紧急处理", priority="urgent")
    assert await _run(app) == 1

    assert len(webhook.wakes()) == 1
    payload = webhook.wakes()[0]
    assert payload["groupId"] == g["id"]
    assert payload["reason"] == "mention"
    assert payload["urgent"] is True
    assert webhook.requests[-1]["headers"]["authorization"] == "Bearer s3cret"
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert rows[0]["status"] == "done"
    ev = await fx.db.execute(
        text("SELECT urgent, kind, result FROM martin.wake_events ORDER BY id DESC LIMIT 1")
    )
    assert ev.one() == (True, "webhook", "done")


@pytest.mark.parametrize("app", [{"wake_dedup_seconds": 600}], indirect=True)
async def test_T_N12_urgent_bypasses_dedup(fx, app, webhook):
    a, b, g = await _setup(fx)
    await _config_webhook(fx, b, webhook)
    name = b["member"]["name"]
    await fx.send(a, g["id"], f"@{name} 1")
    await _run(app)
    await fx.send(a, g["id"], f"@{name} 2")
    await _run(app)
    await fx.send(a, g["id"], f"@{name} 3", priority="urgent")
    await _run(app)

    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert [r["status"] for r in rows] == ["done", "suppressed", "done"]
    assert len(webhook.wakes()) == 2


@pytest.mark.parametrize("app", [{"wake_daily_limit": 1}], indirect=True)
async def test_T_N13_urgent_bypasses_daily_limit(fx, app, webhook):
    a, b, g1 = await _setup(fx, group_name="n-daily-1")
    g2 = await fx.create_group(a, "n-daily-2")
    g3 = await fx.create_group(a, "n-daily-3")
    for g in (g2, g3):
        await fx.client.post(
            "/api/v1/join", json={"invite": g["inviteLink"]}, headers=b["headers"]
        )
    await _config_webhook(fx, b, webhook)
    name = b["member"]["name"]
    await fx.send(a, g1["id"], f"@{name} 1")
    await _run(app)
    await fx.send(a, g2["id"], f"@{name} 2")
    await _run(app)
    await fx.send(a, g3["id"], f"@{name} 3", priority="urgent")
    await _run(app)

    assert (await _queue(fx, b["member"]["id"], g1["id"]))[0]["status"] == "done"
    assert (await _queue(fx, b["member"]["id"], g2["id"]))[0]["status"] == "suppressed"
    assert (await _queue(fx, b["member"]["id"], g3["id"]))[0]["status"] == "done"


@pytest.mark.parametrize("app", [{"wake_retry_delay_seconds": 0}], indirect=True)
async def test_T_N14_retry_once(fx, app, webhook):
    a, b, g = await _setup(fx)
    await _config_webhook(fx, b, webhook)
    webhook.requests.clear()
    webhook.statuses.extend([500, 200])
    await fx.send(a, g["id"], f"@{b['member']['name']} u", priority="urgent")

    await _run(app)
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert rows[0]["status"] == "pending" and rows[0]["attempts"] == 1
    await _run(app)
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert rows[0]["status"] == "done"
    assert len(webhook.wakes()) == 2

    webhook.statuses.extend([500, 500])
    await fx.send(a, g["id"], f"@{b['member']['name']} u2", priority="urgent")
    await _run(app)
    await _run(app)
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert rows[-1]["status"] == "failed"
    assert len(webhook.wakes()) == 4


async def test_T_N15_undeliverable_without_channel(fx, app, webhook):
    a, b, g = await _setup(fx)
    r = await fx.send(a, g["id"], f"@{b['member']['name']} u", priority="urgent")
    assert r.status_code == 200
    assert r.json()["push"]["degraded"][0]["reason"] == "no_channel"
    await _run(app)
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert rows[0]["status"] == "undeliverable"
    assert webhook.wakes() == []


async def test_T_N16_concurrent_dispatch_no_duplicate(fx, app, webhook):
    a, b, g = await _setup(fx)
    await _config_webhook(fx, b, webhook)
    webhook.requests.clear()
    for i in range(5):
        await fx.send(
            a, g["id"], f"@{b['member']['name']} u{i}", priority="urgent", opKey=f"n-conc-{i}"
        )
    await asyncio.gather(_run(app), _run(app))
    assert len(webhook.wakes()) == 5
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert all(r["status"] == "done" for r in rows)


async def test_T_N17_wake_privacy(fx, app, webhook):
    a, b, g = await _setup(fx)
    await _config_webhook(fx, b, webhook, secret="top-secret-key")
    webhook.requests.clear()
    secret_text = "机密正文 payload-token-abc"
    await fx.send(a, g["id"], f"@{b['member']['name']} {secret_text}", priority="urgent")
    await _run(app)
    payload = webhook.wakes()[0]
    assert set(payload) == {"schema", "kind", "groupId", "reason", "urgent", "at"}
    dumped = json.dumps(payload, ensure_ascii=False)
    assert secret_text not in dumped
    assert "top-secret-key" not in dumped
    assert b["token"] not in dumped
    details = await fx.db.execute(text("SELECT detail FROM martin.wake_events"))
    assert all(secret_text not in (d[0] or "") for d in details.all())


async def test_T_N18_recall_urgent(fx, app):
    a, b, g = await _setup(fx)
    r = await fx.send(a, g["id"], f"@{b['member']['name']} u", priority="urgent")
    seq = r.json()["seq"]
    rr = await fx.client.delete(
        f"/api/v1/groups/{g['id']}/messages/{seq}", headers=a["headers"]
    )
    assert rr.status_code == 200
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert len(rows) == 1  # 本批不取消已入队唤醒


# --- 免打扰 ---------------------------------------------------------------


async def test_T_N19_mute_switch_and_reach(fx, webhook):
    a, b, g = await _setup(fx)
    await _config_webhook(fx, b, webhook, muted=True)
    got = (await fx.client.get("/api/v1/wake", headers=b["headers"])).json()["wake"]
    assert got["muted"] is True
    members = (
        await fx.client.get(f"/api/v1/groups/{g['id']}/members", headers=a["headers"])
    ).json()["members"]
    target = next(m for m in members if m["id"] == b["member"]["id"])
    assert target["reach"] == "muted"
    assert target["pushConnected"] is False

    r = await fx.client.patch("/api/v1/wake", json={"muted": False}, headers=b["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["wake"]["muted"] is False
    assert r.json()["wake"]["enabled"] is True  # 切免打扰不必重交 secret
    r = await fx.client.patch("/api/v1/wake", json={"muted": True}, headers=a["headers"])
    assert r.status_code == 400 and r.json()["error"]["code"] == "wake_not_configured"


async def test_T_N20_T_N21_muted_not_pushed_even_urgent(fx, app, webhook):
    a, b, g = await _setup(fx)
    await _config_webhook(fx, b, webhook, muted=True)
    webhook.requests.clear()
    r = await fx.send(a, g["id"], f"@{b['member']['name']} u", priority="urgent")
    assert r.json()["push"]["degraded"][0]["reason"] == "muted"
    await _run(app)
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert rows[0]["status"] == "suppressed" and rows[0]["urgent"] is True
    assert webhook.wakes() == []
    msgs = (await fx.read(b, g["id"])).json()["messages"]
    assert len(msgs) == 1  # 消息照常落库，靠拉取


async def test_T_N22_unmute_no_backfill(fx, app, webhook):
    a, b, g = await _setup(fx)
    await _config_webhook(fx, b, webhook, muted=True)
    webhook.requests.clear()
    name = b["member"]["name"]
    await fx.send(a, g["id"], f"@{name} 历史", priority="urgent")
    await _run(app)
    await _config_webhook(fx, b, webhook, muted=False)
    webhook.requests.clear()
    await _run(app)
    assert webhook.wakes() == []  # 历史不补发
    await fx.send(a, g["id"], f"@{name} 新消息", priority="urgent")
    await _run(app)
    assert len(webhook.wakes()) == 1


# --- SSE 事件流 -----------------------------------------------------------


async def test_T_N23_sse_receives_wake(fx, app, live):
    a, b, g = await _setup(fx)
    await _config_listener(fx, b)
    async with live.stream(
        "GET", "/api/v1/wake/events", headers=b["headers"]
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        await fx.send(a, g["id"], f"@{b['member']['name']} u", priority="urgent")
        await _run(app)
        events = await _read_events(resp, 1)
    assert events[0][1]["groupId"] == g["id"]
    assert events[0][1]["urgent"] is True
    assert events[0][1]["reason"] == "mention"
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert rows[0]["status"] == "done"


async def test_T_N24_sse_reconnect_replay(fx, app, live):
    a, b, g = await _setup(fx)
    await _config_listener(fx, b)
    name = b["member"]["name"]
    await fx.send(a, g["id"], f"@{name} 1", priority="urgent")
    await fx.send(a, g["id"], f"@{name} 2", priority="urgent")
    await _run(app)
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert all(r["status"] == "pending" for r in rows)  # 离线排队

    async with live.stream(
        "GET", "/api/v1/wake/events", headers=b["headers"]
    ) as resp:
        first = await _read_events(resp, 2)
    assert [e[1]["groupId"] for e in first] == [g["id"], g["id"]]
    last_id = first[-1][0]

    await fx.send(a, g["id"], f"@{name} 3", priority="urgent")
    await _run(app)
    async with live.stream(
        "GET",
        "/api/v1/wake/events",
        headers={**b["headers"], "Last-Event-ID": str(last_id)},
    ) as resp:
        again = await _read_events(resp, 1)
    assert len(again) == 1
    assert again[0][0] > last_id


@pytest.mark.parametrize(
    "app", [{"sse_heartbeat_seconds": 0.2, "sse_sweep_seconds": 0.2}], indirect=True
)
async def test_T_N25_sse_heartbeat_and_offline_queue(fx, app, live):
    a, b, g = await _setup(fx)
    await _config_listener(fx, b)
    await fx.send(a, g["id"], f"@{b['member']['name']} u", priority="urgent")
    await _run(app)
    rows = await _queue(fx, b["member"]["id"], g["id"])
    assert rows[0]["status"] == "pending" and rows[0]["attempts"] == 0

    events, got_ping = await _read_stream(live, b, want_events=1, want_ping=True)
    assert len(events) == 1
    assert got_ping


async def test_T_N26_muted_sse_rejected(fx):
    a, b, g = await _setup(fx)
    await _config_listener(fx, b, muted=True)
    r = await fx.client.get("/api/v1/wake/events", headers=b["headers"])
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "wake_muted"


async def test_T_N27_fake_agent_end_to_end(fx, app, webhook):
    a, b, g = await _setup(fx)
    await _config_webhook(fx, b, webhook)
    webhook.requests.clear()

    async def fake_agent(payload):
        # 假 agent 被叫醒后自己拉消息并回复
        msgs = (await fx.read(b, payload["groupId"])).json()["messages"]
        assert msgs and msgs[-1]["priority"] == "urgent"
        await fx.send(b, payload["groupId"], "收到紧急，已处理", ignore_new=True)

    webhook.on_wake = fake_agent
    await fx.send(a, g["id"], f"@{b['member']['name']} 线上故障", priority="urgent")
    await _run(app)

    msgs = (await fx.read(a, g["id"])).json()["messages"]
    assert any(m["text"] == "收到紧急，已处理" for m in msgs)
    assert (await _queue(fx, b["member"]["id"], g["id"]))[0]["status"] == "done"
