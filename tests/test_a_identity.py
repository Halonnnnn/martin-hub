import asyncio
import datetime as dt
import re

import pytest
from sqlalchemy import text

from martin.security import sha256_hex

pytestmark = pytest.mark.asyncio


async def test_T_A01_pair_success_returns_token_once(fx):
    await fx.make_code("PAIR-AAAA")
    r = await fx.pair("PAIR-AAAA", "A Bot")
    assert r.status_code == 200
    data = r.json()
    assert data["token"].startswith("martin_")
    assert data["name"] == "A Bot"

    row = await fx.db.execute(
        text("SELECT code_hash, used_at IS NOT NULL FROM martin.pairing_codes")
    )
    hash_, used = row.one()
    assert used is True
    tokens = await fx.db.execute(text("SELECT token_hash FROM martin.members"))
    stored = tokens.scalar_one()
    assert stored != data["token"]
    assert stored == sha256_hex(data["token"])


async def test_T_A02_invalid_expired_used_codes(fx):
    r = await fx.pair("NOPE-ZZZZ", "A Bot")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "invalid_code"

    await fx.make_code("OLD-CODE1", expired=True)
    r = await fx.pair("OLD-CODE1", "A Bot")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "code_expired"

    await fx.make_code("USE-CODE1")
    r1 = await fx.pair("USE-CODE1", "First Bot")
    assert r1.status_code == 200
    r2 = await fx.pair("USE-CODE1", "Second Bot")
    assert r2.status_code == 403
    assert r2.json()["error"]["code"] == "code_used"


async def test_T_A03_pairing_code_consume_atomic(fx):
    await fx.make_code("RACE-COD1")
    results = await asyncio.gather(
        *[fx.pair("RACE-COD1", f"Racer {i}") for i in range(5)]
    )
    ok = [r for r in results if r.status_code == 200]
    used_err = [r for r in results if r.json().get("error", {}).get("code") == "code_used"]
    assert len(ok) == 1
    assert len(used_err) == 4


async def test_T_A04_invalid_token_401(fx):
    for headers in ({}, {"Authorization": "Bearer martin_bogus"}):
        r = await fx.client.get("/api/v1/me", headers=headers)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "unauthorized"


async def test_T_A05_me_returns_identity(fx):
    m = await fx.new_member("ME-CODE1", "Me Bot", platform="claude")
    r = await fx.client.get("/api/v1/me", headers=m["headers"])
    assert r.status_code == 200
    member = r.json()["member"]
    assert member["name"] == "Me Bot"
    assert member["platform"] == "claude"
    assert member["human"] is False


async def test_T_A06_member_exists_lists_groups(fx):
    owner = await fx.make_owner("changyu")
    m = await fx.new_member("EX-CODE1", "Twin Bot", owner_id=owner.id)
    g = await fx.create_group(m, "twins")
    await fx.make_code("EX-CODE2", owner_id=owner.id)
    r = await fx.pair("EX-CODE2", "Twin Bot")
    assert r.status_code == 409
    data = r.json()
    assert data["error"]["code"] == "member_exists"
    assert any(gr["id"] == g["id"] for gr in data["groups"])


async def test_T_A07_member_active_within_window(fx):
    owner = await fx.make_owner("changyu")
    m = await fx.new_member("ACT-COD1", "Busy Bot", owner_id=owner.id)
    await fx.client.get("/api/v1/me", headers=m["headers"])
    await fx.make_code("ACT-COD2")
    r = await fx.pair("ACT-COD2", "Busy Bot", reclaim=True)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "member_active"


async def test_T_A08_reclaim_replaces_token_keeps_groups(fx):
    owner = await fx.make_owner("changyu")
    m1 = await fx.new_member("RE-CODE1", "Ghost Bot", owner_id=owner.id)
    g = await fx.create_group(m1, "haunted")

    member_row = await fx.db.execute(
        text("SELECT id FROM martin.members WHERE name='Ghost Bot'")
    )
    mid = member_row.scalar()
    await fx.db.execute(
        text("UPDATE martin.members SET last_seen_at = now() - interval '1 hour' WHERE id=:i"),
        {"i": mid},
    )
    await fx.db.commit()

    await fx.make_code("RE-CODE2")
    r = await fx.pair("RE-CODE2", "Ghost Bot", reclaim=True)
    assert r.status_code == 200
    new_token = r.json()["token"]

    r_new = await fx.client.get("/api/v1/me", headers={"Authorization": f"Bearer {new_token}"})
    assert r_new.status_code == 200
    r_old = await fx.client.get("/api/v1/me", headers=m1["headers"])
    assert r_old.status_code == 401

    groups = await fx.client.get("/api/v1/groups", headers={"Authorization": f"Bearer {new_token}"})
    assert any(x["id"] == g["id"] for x in groups.json()["groups"])


async def test_T_A09_revoke_token(fx):
    m = await fx.new_member("RV-CODE1", "Revoke Bot")
    r = await fx.client.delete("/api/v1/token", headers=m["headers"])
    assert r.status_code == 200
    assert r.json()["revoked"] is True
    r2 = await fx.client.get("/api/v1/me", headers=m["headers"])
    assert r2.status_code == 401
    r3 = await fx.client.get("/api/v1/me", headers=m["headers"])
    assert r3.status_code == 401


async def test_T_A10_patch_me(fx):
    m = await fx.new_member("PM-CODE1", "Patch Bot")
    r = await fx.client.patch(
        "/api/v1/me", json={"intro": "代码审查专家"}, headers=m["headers"]
    )
    assert r.status_code == 200
    assert r.json()["member"]["intro"] == "代码审查专家"

    r = await fx.client.patch(
        "/api/v1/me", json={"intro": "x" * 301}, headers=m["headers"]
    )
    assert r.status_code == 422

    m2 = await fx.new_member("PM-CODE2", "Patch Two")
    r = await fx.client.patch(
        "/api/v1/me", json={"name": "Patch Bot"}, headers=m2["headers"]
    )
    assert r.status_code == 409


async def test_T_A11_cut_endpoints_return_404(fx):
    m = await fx.new_member("CU-CODE1", "Cut Bot")
    for path in ("/api/v1/connect", "/api/v1/connect/verify", "/api/v1/cards/ABC", "/api/v1/invites"):
        r = await fx.client.get(path, headers=m["headers"])
        assert r.status_code == 404, path


async def test_T_A12_public_ids_stable_and_path_lookup(fx):
    a = await fx.new_member("A12-A", "Public A")
    b = await fx.new_member("A12-B", "Public B")
    g = await fx.create_group(a, "public-ids")
    aid = a["member"]["id"]
    gid = g["id"]
    assert re.fullmatch(r"a[a-z2-9]{8}", aid)
    assert re.fullmatch(r"a[a-z2-9]{8}", b["member"]["id"])
    assert re.fullmatch(r"g[a-z2-9]{8}", gid)

    me = await fx.client.get("/api/v1/me", headers=a["headers"])
    assert me.json()["member"]["id"] == aid
    groups = await fx.client.get("/api/v1/groups", headers=a["headers"])
    assert any(x["id"] == gid for x in groups.json()["groups"])
    members = await fx.client.get(f"/api/v1/groups/{gid}/members", headers=a["headers"])
    assert {m["id"] for m in members.json()["members"]} == {aid}

    # agent 不再有好友关系（好友只连接用户）；成员码仍用于群成员解析
    r = await fx.client.post(
        "/api/v1/friends", json={"to": b["member"]["id"], "note": ""}, headers=a["headers"]
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "friends_are_people"
    assert (await fx.send(a, gid, "hello")).status_code == 200

    # 未知/非法码统一 404，不再接受旧数字 id
    assert (await fx.send(a, "g00000000", "x")).status_code == 404
    assert (await fx.send(a, "1", "x")).status_code == 404
