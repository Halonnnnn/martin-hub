import datetime as dt

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_member
from ..errors import AppError
from ..models import Group, GroupMember, Member, PairingCode
from ..schemas import MePatch, PairRequest, PresenceUpdate
from ..security import gen_token, sha256_hex
from ..serialize import group_dict, member_dict
from ..services import ensure_group_capacity, reach_for_members
from ..timeutil import as_utc

router = APIRouter()


async def get_db(request: Request):
    async with request.app.state.sessionmaker() as session:
        yield session


async def _reach(db: AsyncSession, member) -> dict:
    return await reach_for_members(db, [member])


@router.post("/pair")
async def pair(body: PairRequest, request: Request, db: AsyncSession = Depends(get_db)):
    settings = request.app.state.settings
    code_hash = sha256_hex(body.code.strip().upper())

    row = await db.execute(select(PairingCode).where(PairingCode.code_hash == code_hash))
    code_row = row.scalar_one_or_none()
    now = dt.datetime.now(dt.timezone.utc)
    if code_row is None:
        raise AppError("invalid_code", "配对码不存在", 403)
    if code_row.used_at is not None or code_row.revoked_at is not None:
        raise AppError("code_used", "配对码已被使用或撤销", 403)
    if code_row.expires_at is not None and as_utc(code_row.expires_at) < now:
        raise AppError("code_expired", "配对码已过期", 403)

    existing_row = await db.execute(select(Member).where(Member.name == body.name))
    existing = existing_row.scalar_one_or_none()

    if existing is not None and not body.reclaim:
        groups = await _member_groups(db, existing.id)
        raise AppError(
            "member_exists",
            "同名成员已存在，确认是本人后请带 reclaim: true 重试",
            409,
            payload={"groups": groups},
        )
    if existing is not None and body.reclaim:
        if (
            existing.last_seen_at is not None
            and (now - existing.last_seen_at).total_seconds()
            < settings.member_active_window_seconds
        ):
            raise AppError(
                "member_active",
                "同名成员刚刚活跃，疑似仍在运行；请让用户在 Web 页面确认重连",
                409,
            )

    consume = await db.execute(
        update(PairingCode)
        .where(
            PairingCode.id == code_row.id,
            PairingCode.used_at.is_(None),
            PairingCode.revoked_at.is_(None),
        )
        .values(used_at=now)
        .returning(PairingCode.id)
    )
    if consume.scalar_one_or_none() is None:
        raise AppError("code_used", "配对码已被使用", 403)

    token = gen_token()
    token_hash = sha256_hex(token)

    if existing is not None:
        member = existing
        member.token_hash = token_hash
        member.token_prefix = token[:10]
        member.token_revoked_at = None
        member.platform = body.platform
        member.presence = "offline"
        member.presence_at = now
    else:
        member = Member(
            name=body.name,
            platform=body.platform,
            owner_id=code_row.owner_id,
            human=False,
            token_hash=token_hash,
            token_prefix=token[:10],
        )
        db.add(member)

    await db.commit()
    await db.refresh(member)

    join_result = None
    if code_row.group_id is not None:
        joined = await _ensure_member_in_group(
            db,
            code_row.group_id,
            member.id,
            limit=request.app.state.settings.members_per_group_limit,
        )
        await db.commit()
        group = await db.get(Group, code_row.group_id)
        join_result = {
            "groupId": group.public_id,
            "groupName": group.name,
            "joined": joined,
        }

    await request.app.state.cache.delete(f"martin:token:{token_hash}")
    return {
        "schema": 1,
        "token": token,
        "name": member.name,
        "member": member_dict(member, member),
        "join": join_result,
    }


async def _member_groups(db: AsyncSession, member_id: int) -> list[dict]:
    rows = await db.execute(
        select(Group).join(GroupMember, GroupMember.group_id == Group.id).where(
            GroupMember.member_id == member_id, GroupMember.removed_at.is_(None)
        )
    )
    return [group_dict(g) for g in rows.scalars().all()]


async def _ensure_member_in_group(
    db: AsyncSession, group_id: int, member_id: int, limit: int | None = None
) -> bool:
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id, GroupMember.member_id == member_id
        )
    )
    gm = row.scalar_one_or_none()
    if gm is not None and gm.removed_at is None:
        return False
    await ensure_group_capacity(db, group_id, limit)
    if gm is None:
        db.add(GroupMember(group_id=group_id, member_id=member_id, role="member"))
        return True
    gm.removed_at = None
    return True


@router.get("/me")
async def me(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    reach = (await _reach(db, member)).get(member.id, "away")
    return {"schema": 1, "member": member_dict(member, member, reach=reach)}


@router.patch("/me")
async def patch_me(
    body: MePatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    if body.name is not None and body.name != member.name:
        dup = await db.execute(select(Member).where(Member.name == body.name))
        if dup.scalar_one_or_none() is not None:
            raise AppError("member_exists", "该名字已被占用", 409)
        member.name = body.name
    if body.intro is not None:
        member.intro = body.intro
    await db.commit()
    return {"schema": 1, "member": member_dict(member, member)}


def _presence_payload(member: Member) -> dict:
    return {
        "schema": 1,
        "presence": member.presence,
        "updatedAt": member.presence_at.isoformat() if member.presence_at else None,
        "member": member_dict(member, member),
    }


@router.get("/presence")
async def get_presence(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """读取自己的在线状态；agent 用 GET /me 时也会带同一个字段。"""
    member = await current_member(request, db)
    return _presence_payload(member)


@router.put("/presence")
async def set_presence(
    body: PresenceUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """agent 自行上报在线状态；Web「接入 Agent」页、群成员列表与 agent 私聊据此显示绿/灰点。"""
    member = await current_member(request, db)
    member.presence = body.status
    member.presence_at = dt.datetime.now(dt.timezone.utc)
    await db.commit()
    return _presence_payload(member)


@router.delete("/token")
async def revoke_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    member.token_revoked_at = dt.datetime.now(dt.timezone.utc)
    member.presence = "offline"
    member.presence_at = member.token_revoked_at
    await db.commit()
    await request.app.state.cache.delete(f"martin:token:{sha256_hex(token)}")
    return {"schema": 1, "revoked": True}
