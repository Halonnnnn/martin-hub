import datetime as dt

import pytest

pytestmark = pytest.mark.asyncio


async def test_T_B01_create_group(fx):
    m = await fx.new_member("B01-CODE1", "Owner Bot")
    g = await fx.create_group(m, "dev", topic="开发协作", category="tech")
    assert g["visibility"] == "link"
    assert g["inviteLink"].startswith("/join/")
    assert g["dissolved"] is False

    r = await fx.client.post(
        "/api/v1/groups", json={"name": "nocat"}, headers=m["headers"]
    )
    assert r.status_code == 200
    assert r.json()["group"]["category"] is None

    r = await fx.client.post(
        "/api/v1/groups", json={"name": "pub", "visibility": "public"}, headers=m["headers"]
    )
    assert r.status_code == 400


async def test_T_B02_same_name_conflict(fx):
    m = await fx.new_member("B02-CODE1", "Owner Bot")
    await fx.create_group(m, "same-name")
    r = await fx.client.post("/api/v1/groups", json={"name": "same-name"}, headers=m["headers"])
    assert r.status_code == 409
    data = r.json()
    assert data["error"]["code"] == "group_exists"
    assert len(data["groups"]) == 1

    r = await fx.client.post(
        "/api/v1/groups",
        json={"name": "same-name", "allowSameName": True},
        headers=m["headers"],
    )
    assert r.status_code == 200


async def test_T_B03_join_by_id_same_owner(fx):
    owner = await fx.make_owner("changyu")
    a = await fx.new_member("B03-CODA", "Alpha Bot", owner_id=owner.id)
    b = await fx.new_member("B03-CODB", "Beta Bot", owner_id=owner.id)
    g = await fx.create_group(a, "home")

    stranger = await fx.new_member("B03-CODS", "Stranger Bot")
    r = await fx.client.post(f"/api/v1/groups/{g['id']}/join", json={}, headers=stranger["headers"])
    assert r.status_code == 403

    r = await fx.client.post(f"/api/v1/groups/{g['id']}/join", json={}, headers=b["headers"])
    assert r.status_code == 200
    assert r.json()["joined"] is True

    r2 = await fx.client.post(f"/api/v1/groups/{g['id']}/join", json={}, headers=b["headers"])
    assert r2.status_code == 200
    assert r2.json()["joined"] is False

    members = await fx.client.get(f"/api/v1/groups/{g['id']}/members", headers=a["headers"])
    names = [x["name"] for x in members.json()["members"]]
    assert "Beta Bot" in names and "Stranger Bot" not in names


async def test_T_B04_join_by_invite_link(fx):
    a = await fx.new_member("B04-CODA", "Host Bot")
    g = await fx.create_group(a, "open-door", topic="自由加入")
    link = g["inviteLink"]

    b = await fx.new_member("B04-CODB", "Guest Bot")
    r = await b_join(fx, b, link)
    assert r.status_code == 200
    assert r.json()["joined"] is True

    r2 = await b_join(fx, b, f"http://example.com{link}")
    assert r2.status_code == 200

    r3 = await b_join(fx, b, "/join/BOGUSCODE123")
    assert r3.status_code == 404


async def b_join(fx, member, link):
    return await fx.client.post(
        "/api/v1/join", json={"invite": link}, headers=member["headers"]
    )


async def test_T_B05_reset_invite_owner_only(fx):
    a = await fx.new_member("B05-CODA", "Host Bot")
    g = await fx.create_group(a, "private")
    old_link = g["inviteLink"]

    b = await fx.new_member("B05-CODB", "Other Bot")
    r = await fx.client.post(f"/api/v1/groups/{g['id']}/invite", json={}, headers=b["headers"])
    assert r.status_code == 403

    r = await fx.client.post(f"/api/v1/groups/{g['id']}/invite", json={}, headers=a["headers"])
    assert r.status_code == 200
    new_link = r.json()["inviteLink"]
    assert new_link != old_link

    r_old = await b_join(fx, b, old_link)
    assert r_old.status_code == 404
    r_new = await b_join(fx, b, new_link)
    assert r_new.status_code == 200


async def test_T_B06_leave_rules(fx):
    a = await fx.new_member("B06-CODA", "Host Bot")
    g = await fx.create_group(a, "crew")
    b = await fx.new_member("B06-CODB", "Crew Bot")
    await b_join(fx, b, g["inviteLink"])

    r = await fx.client.post(f"/api/v1/groups/{g['id']}/leave", json={}, headers=a["headers"])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "owner_cannot_leave"

    r = await fx.client.post(f"/api/v1/groups/{g['id']}/leave", json={}, headers=b["headers"])
    assert r.status_code == 200

    members = await fx.client.get(f"/api/v1/groups/{g['id']}/members", headers=a["headers"])
    assert "Crew Bot" not in [x["name"] for x in members.json()["members"]]


async def test_T_B07_remove_and_dissolve(fx):
    a = await fx.new_member("B07-CODA", "Host Bot")
    g = await fx.create_group(a, "temp")
    b = await fx.new_member("B07-CODB", "Temp Bot")
    await b_join(fx, b, g["inviteLink"])

    r = await fx.client.delete(
        f"/api/v1/groups/{g['id']}/members/{b['member']['id']}", headers=b["headers"]
    )
    assert r.status_code == 403

    r = await fx.client.delete(
        f"/api/v1/groups/{g['id']}/members/{b['member']['id']}", headers=a["headers"]
    )
    assert r.status_code == 200

    r = await fx.client.delete(f"/api/v1/groups/{g['id']}", headers=a["headers"])
    assert r.status_code == 200
    assert r.json()["dissolved"] is True

    r = await fx.send(a, g["id"], "还在吗")
    assert r.status_code == 404

    r = await fx.client.delete(f"/api/v1/groups/{g['id']}", headers=a["headers"])
    assert r.status_code == 404


async def test_T_B08_members_list_fields(fx):
    owner = await fx.make_owner("changyu")
    a = await fx.new_member("B08-CODA", "Host Bot", owner_id=owner.id)
    g = await fx.create_group(a, "about")

    members = await fx.client.get(f"/api/v1/groups/{g['id']}/members", headers=a["headers"])
    data = members.json()["members"]
    host = next(x for x in data if x["name"] == "Host Bot")
    assert host["platform"] == "test"
    assert host["human"] is False
    assert host["ownerId"] == owner.id
    assert host["role"] == "owner"
    assert host["reach"] == "away"

    r = await fx.client.post("/api/v1/web/login", json={"username": "changyu", "password": "pw123456"})
    assert r.status_code == 200
    r = await fx.client.post(f"/api/v1/web/groups/{g['id']}/join")
    assert r.status_code == 200
    members = await fx.client.get(f"/api/v1/groups/{g['id']}/members", headers=a["headers"])
    human = next(x for x in members.json()["members"] if x["human"])
    assert human["name"] == "changyu"


async def test_T_B09_join_needs_no_answer(fx):
    a = await fx.new_member("B09-CODA", "Host Bot")
    g = await fx.create_group(a, "no-question")
    b = await fx.new_member("B09-CODB", "Guest Bot")
    r = await b_join(fx, b, g["inviteLink"])
    assert r.status_code == 200
