"""P0 批次：幂等体校验、恢复分流、探针、租约与 fence。"""
import asyncio
import datetime as dt

import pytest
from sqlalchemy import select

from martin.models import ReceiverLease, WakeConfig, WakeEvent, WakeQueue

pytestmark = pytest.mark.asyncio


async def _setup(fx, names=("Q Bot A", "Q Bot B"), group_name="q-p0"):
    a = await fx.new_member("Q-SEED-A", names[0])
    b = await fx.new_member("Q-SEED-B", names[1])
    g = await fx.create_group(a, group_name)
    r = await fx.client.post(
        "/api/v1/join", json={"invite": g["inviteLink"]}, headers=b["headers"]
    )
    assert r.status_code == 200, r.text
    return a, b, g


# --- P0-1 幂等体校验 --------------------------------------------------------


async def test_T_Q01_opkey_different_body_conflict(fx):
    a, _b, g = await _setup(fx)
    r1 = await fx.send(a, g["id"], "v1", opKey="q-op-1")
    assert r1.status_code == 200

    r2 = await fx.send(a, g["id"], "v2", opKey="q-op-1")
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "idempotency_conflict"

    r3 = await fx.send(a, g["id"], "v1", opKey="q-op-1")
    assert r3.status_code == 200
    assert r3.json()["idempotent"] is True
    assert r3.json()["seq"] == r1.json()["seq"]


async def test_T_Q02_opkey_retry_survives_new_messages(fx):
    a, b, g = await _setup(fx)
    r1 = await fx.send(a, g["id"], "same", opKey="q-op-2")
    await fx.send(b, g["id"], "another message")
    # 重试不应被"发送前有新消息"门禁打断
    r2 = await fx.send(a, g["id"], "same", opKey="q-op-2")
    assert r2.status_code == 200
    assert r2.json()["idempotent"] is True
    assert r2.json()["seq"] == r1.json()["seq"]


# --- P0-2 恢复分流 ----------------------------------------------------------


async def _stale_dispatching(fx, member_id: str, group_id: str, kind: str) -> WakeQueue:
    member_id = await fx.mid(member_id)
    group_id = await fx.gid(group_id)
    cfg = await fx.db.get(WakeConfig, member_id)
    if cfg is None:
        cfg = WakeConfig(member_id=member_id, kind=kind, url="http://127.0.0.1:1/hook")
        fx.db.add(cfg)
    row = WakeQueue(
        member_id=member_id,
        group_id=group_id,
        reason="mention",
        status="dispatching",
        next_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1),
    )
    fx.db.add(row)
    await fx.db.commit()
    await fx.db.refresh(row)
    return row


async def test_T_Q03_recover_splits_by_channel(fx, app):
    a, b, g = await _setup(fx)
    hook_row = await _stale_dispatching(fx, b["member"]["id"], g["id"], "webhook")
    listener_row = await _stale_dispatching(fx, a["member"]["id"], g["id"], "listener")

    await app.state.dispatcher._recover()

    await fx.db.refresh(hook_row)
    await fx.db.refresh(listener_row)
    assert hook_row.status == "ambiguous"
    assert listener_row.status == "pending"

    events = (
        await fx.db.execute(
            select(WakeEvent).where(WakeEvent.result == "ambiguous")
        )
    ).scalars().all()
    assert len(events) == 1 and events[0].member_id == hook_row.member_id

    # ambiguous 不会被重新派发
    await app.state.dispatcher.run_once()
    await fx.db.refresh(hook_row)
    assert hook_row.status == "ambiguous"


# --- P0-4 探针 --------------------------------------------------------------


async def test_T_Q04_probe_pending_and_cursor(fx):
    a, b, g = await _setup(fx)

    r = await fx.client.get("/api/v1/probe", headers=b["headers"])
    assert r.status_code == 200
    assert r.json()["pending"] is False
    assert r.json()["cursor"] == 0

    sent = await fx.send(a, g["id"], "hello")
    r = await fx.client.get("/api/v1/probe", headers=b["headers"])
    assert r.json()["pending"] is True
    assert r.json()["cursor"] == sent.json()["seq"]

    await fx.read(b, g["id"])
    r = await fx.client.get("/api/v1/probe", headers=b["headers"])
    assert r.json()["pending"] is False

    r = await fx.client.get("/api/v1/probe")
    assert r.status_code == 401


async def test_T_Q05_inbox_wait_wakes_on_message(fx):
    a, b, g = await _setup(fx)
    cursor = (await fx.client.get("/api/v1/inbox", headers=b["headers"])).json()["cursor"]

    task = asyncio.create_task(
        fx.client.get(
            "/api/v1/inbox", params={"since": cursor, "wait": 10}, headers=b["headers"]
        )
    )
    await asyncio.sleep(0.1)
    await fx.send(a, g["id"], "wake up")
    r = await asyncio.wait_for(task, timeout=3)
    assert r.status_code == 200
    assert r.json()["cursor"] > cursor


# --- P0-3 租约与 fence -------------------------------------------------------

H1 = "test-holder-aaaa-1"
H2 = "test-holder-bbbb-2"


async def _acquire(fx, member, holder=H1):
    return await fx.client.post(
        "/api/v1/wake/lease", json={"holder": holder}, headers=member["headers"]
    )


async def test_T_Q06_lease_exclusive_and_renew(fx):
    _a, b, _g = await _setup(fx)
    r1 = await _acquire(fx, b)
    assert r1.status_code == 200
    assert r1.json()["acquired"] is True
    fence = r1.json()["fence"]

    r2 = await _acquire(fx, b, H2)
    assert r2.json()["acquired"] is False

    renew = await _acquire(fx, b, H1)
    assert renew.json()["acquired"] is True
    assert renew.json()["fence"] == fence

    bad = await fx.client.request(
        "DELETE",
        "/api/v1/wake/lease",
        json={"holder": H1, "fence": "wrong"},
        headers=b["headers"],
    )
    assert bad.json()["released"] is False

    ok = await fx.client.request(
        "DELETE",
        "/api/v1/wake/lease",
        json={"holder": H1, "fence": fence},
        headers=b["headers"],
    )
    assert ok.json()["released"] is True

    r3 = await _acquire(fx, b, H2)
    assert r3.json()["acquired"] is True
    assert r3.json()["fence"] != fence


async def test_T_Q07_lease_expired_takeover(fx):
    _a, b, _g = await _setup(fx)
    r1 = await _acquire(fx, b)
    fence_old = r1.json()["fence"]

    lease = await fx.db.get(ReceiverLease, await fx.mid(b["member"]["id"]))
    lease.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    await fx.db.commit()

    r2 = await _acquire(fx, b, H2)
    assert r2.json()["acquired"] is True
    assert r2.json()["fence"] != fence_old


async def test_T_Q08_fence_checked_only_when_sent(fx):
    a, b, g = await _setup(fx)
    lease = (await _acquire(fx, b)).json()
    fence = lease["fence"]

    bad = await fx.send(
        b, g["id"], "x", opKey="q-fence-bad",
    )
    assert bad.status_code == 200  # 不带头不校验

    wrong_headers = {**b["headers"], "X-Martin-Fence": "wrong"}
    r = await fx.client.post(
        f"/api/v1/groups/{g['id']}/messages",
        json={"text": "y", "opKey": "q-fence-wrong"},
        headers=wrong_headers,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "lease_lost"

    good_headers = {**b["headers"], "X-Martin-Fence": fence}
    r = await fx.client.post(
        f"/api/v1/groups/{g['id']}/messages",
        json={"text": "z", "opKey": "q-fence-good"},
        headers=good_headers,
    )
    assert r.status_code == 200

    r = await fx.client.post(
        f"/api/v1/groups/{g['id']}/read",
        json={"all": True},
        headers=wrong_headers,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "lease_lost"
