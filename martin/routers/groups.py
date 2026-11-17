import datetime as dt

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_member
from ..errors import AppError, bad_request, forbidden, not_found
from ..models import Group, GroupMember, Member, Message
from ..schemas import GroupCreate, GroupPatch, JoinByInvite
from ..security import gen_invite_code
from ..serialize import group_dict, member_dict
from ..services import (
    active_membership,
    ensure_group_capacity,
    get_group_or_404,
    push_states,
    reach_for_members,
    reject_temp_group_ops,
)

router = APIRouter()


async def get_db(request: Request):
    async with request.app.state.sessionmaker() as session:
        yield session


VALID_CATEGORIES = {
    "investing", "business", "tech", "marketing", "legal", "research", "health",
    "career", "food", "travel", "sports", "games", "learning", "arts", "parents",
    "neighbors", "free_stuff", "other",
}


async def join_group(
    db: AsyncSession,
    group: Group,
    member: Member,
    role: str = "member",
    limit: int | None = None,
) -> bool:
    reject_temp_group_ops(group)
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id, GroupMember.member_id == member.id
        )
    )
    gm = row.scalar_one_or_none()
    if gm is not None and gm.removed_at is None:
        return False
    await ensure_group_capacity(db, group.id, limit)
    if gm is None:
        db.add(GroupMember(group_id=group.id, member_id=member.id, role=role))
        await db.commit()
        return True
    gm.removed_at = None
    await db.commit()
    return True


async def group_from_invite(db: AsyncSession, invite: str) -> Group:
    """接受邀请链接或群码；群码与链接都是入群凭证。"""
    code = invite.strip()
    if "/join/" in code:
        code = code.rsplit("/join/", 1)[1].strip("/? ")
    row = await db.execute(
        select(Group).where(Group.invite_code == code, Group.dissolved_at.is_(None))
    )
    group = row.scalar_one_or_none()
    if group is None:
        raise not_found("邀请链接无效")
    if group.kind in ("direct", "temp"):
        raise not_found("邀请链接无效")
    return group


async def add_system_message(db: AsyncSession, group: Group, author: Member, text: str) -> None:
    max_seq_row = await db.execute(
        select(Message.seq).where(Message.group_id == group.id).order_by(Message.seq.desc()).limit(1)
    )
    last = max_seq_row.scalar_one_or_none() or 0
    db.add(
        Message(
            group_id=group.id,
            seq=last + 1,
            author_id=author.id,
            type="system",
            text=text,
        )
    )


async def require_owner(db: AsyncSession, group: Group, member: Member) -> None:
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == member.id,
            GroupMember.removed_at.is_(None),
        )
    )
    gm = row.scalar_one_or_none()
    if gm is None or gm.role != "owner":
        raise forbidden("仅群主可执行此操作")


async def create_group_for(db: AsyncSession, member: Member, body: GroupCreate) -> Group:
    if body.visibility != "link":
        raise bad_request("一期仅支持 link 可见性", "unsupported_visibility")
    if body.category is not None and body.category not in VALID_CATEGORIES:
        raise bad_request("category 取值不合法", "invalid_category")

    dup = await db.execute(
        select(Group).where(Group.name == body.name, Group.dissolved_at.is_(None))
    )
    if dup.scalar_one_or_none() is not None and not body.allowSameName:
        groups = [
            group_dict(g)
            for g in (await db.execute(
                select(Group).where(Group.name == body.name, Group.dissolved_at.is_(None))
            )).scalars().all()
        ]
        raise AppError(
            "group_exists", "同名群已存在", 409, payload={"groups": groups}
        )

    group = Group(
        name=body.name,
        topic=body.topic,
        rules=body.rules,
        visibility=body.visibility,
        category=body.category,
        invite_code=gen_invite_code(),
        owner_member_id=member.id,
    )
    db.add(group)
    await db.flush()
    db.add(
        GroupMember(group_id=group.id, member_id=member.id, role="owner")
    )
    await db.commit()
    await db.refresh(group)
    return group


@router.post("/groups")
async def create_group(
    body: GroupCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = await create_group_for(db, member, body)
    return {
        "schema": 1,
        "group": group_dict(group, invite_code=group.invite_code),
    }


@router.get("/groups")
async def list_groups(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    rows = await db.execute(
        select(Group, GroupMember.last_read_seq)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(
            GroupMember.member_id == member.id,
            GroupMember.removed_at.is_(None),
            Group.dissolved_at.is_(None),
        )
    )
    result = []
    for group, last_read in rows.all():
        unread_row = await db.execute(
            select(Message.seq)
            .where(
                Message.group_id == group.id,
                Message.seq > last_read,
                Message.recalled_at.is_(None),
                Message.author_id != member.id,
            )
            .order_by(Message.seq.desc())
            .limit(200)
        )
        unread = len(unread_row.scalars().all())
        d = group_dict(group)
        d["unread"] = unread
        d["role"] = "member"
        result.append(d)
    return {"schema": 1, "groups": result}


@router.get("/groups/{group_id}")
async def get_group(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = (
        await db.execute(select(Group).where(Group.public_id == group_id))
    ).scalar_one_or_none()
    if group is None:
        raise not_found("group not found")
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == member.id,
            GroupMember.removed_at.is_(None),
        )
    )
    is_member = row.scalar_one_or_none() is not None
    if group.dissolved_at is not None:
        raise not_found("group dissolved")
    if is_member:
        return {"schema": 1, "group": group_dict(group, invite_code=group.invite_code)}
    return {
        "schema": 1,
        "group": {
            "id": group.public_id,
            "name": group.name,
            "topic": group.topic,
            "rules": group.rules,
            "visibility": group.visibility,
            "category": group.category,
        },
    }


@router.patch("/groups/{group_id}")
async def patch_group(
    group_id: str,
    body: GroupPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = await get_group_or_404(db, group_id)
    reject_temp_group_ops(group)
    await require_owner(db, group, member)
    if body.name is not None and body.name != group.name:
        dup = await db.execute(
            select(Group).where(
                Group.name == body.name, Group.dissolved_at.is_(None), Group.id != group.id
            )
        )
        if dup.scalar_one_or_none() is not None:
            raise AppError("group_exists", "同名群已存在", 409)
        group.name = body.name
    if body.topic is not None:
        group.topic = body.topic
    if body.rules is not None:
        group.rules = body.rules
    await db.commit()
    return {"schema": 1, "group": group_dict(group, invite_code=group.invite_code)}


@router.post("/groups/{group_id}/join")
async def join_by_id(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = await get_group_or_404(db, group_id)
    reject_temp_group_ops(group)
    if group.kind == "direct":
        raise forbidden("私聊会话不可加入")
    owner_member = await db.get(Member, group.owner_member_id)
    same_owner = (
        owner_member is not None
        and member.owner_id is not None
        and member.owner_id == owner_member.owner_id
    )
    if group.visibility != "public" and not same_owner:
        raise forbidden("非公开群需邀请链接加入")
    joined = await join_group(
        db, group, member, limit=request.app.state.settings.members_per_group_limit
    )
    return {
        "schema": 1,
        "joined": joined,
        "group": group_dict(group),
        "note": "已加入，打个招呼吧" if joined else "已在群中",
    }


@router.post("/groups/{group_id}/leave")
async def leave_group(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = await get_group_or_404(db, group_id)
    reject_temp_group_ops(group)
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == member.id,
            GroupMember.removed_at.is_(None),
        )
    )
    gm = row.scalar_one_or_none()
    if gm is None:
        raise not_found("not a member")
    if gm.role == "owner":
        raise bad_request("群主请先解散群或转移群主后再离开", "owner_cannot_leave")
    gm.removed_at = dt.datetime.now(dt.timezone.utc)
    await add_system_message(db, group, member, f"{member.name} 离开了群")
    await db.commit()
    return {"schema": 1, "left": True}


@router.post("/groups/{group_id}/invite")
async def reset_invite(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = await get_group_or_404(db, group_id)
    reject_temp_group_ops(group)
    if group.kind == "direct":
        raise bad_request("私聊会话没有邀请链接", "direct_no_invite")
    await require_owner(db, group, member)
    group.invite_code = gen_invite_code()
    await db.commit()
    return {
        "schema": 1,
        "inviteLink": f"/join/{group.invite_code}",
        "inviteMessage": group_dict(group, invite_code=group.invite_code)["inviteMessage"],
    }


@router.delete("/groups/{group_id}/members/{member_id}")
async def remove_member(
    group_id: str,
    member_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = await get_group_or_404(db, group_id)
    reject_temp_group_ops(group)
    await require_owner(db, group, member)
    target = (
        await db.execute(select(Member).where(Member.public_id == member_id))
    ).scalar_one_or_none()
    if target is None:
        raise not_found("member not in group")
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == target.id,
            GroupMember.removed_at.is_(None),
        )
    )
    gm = row.scalar_one_or_none()
    if gm is None:
        raise not_found("member not in group")
    if target.id == group.owner_member_id:
        raise bad_request("不能移除群主", "cannot_remove_owner")
    gm.removed_at = dt.datetime.now(dt.timezone.utc)
    await add_system_message(
        db, group, member, f"{target.name} 被移出群"
    )
    await db.commit()
    return {"schema": 1, "removed": True}


@router.delete("/groups/{group_id}")
async def dissolve_group(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = await get_group_or_404(db, group_id)
    reject_temp_group_ops(group)
    if group.kind == "direct":
        await active_membership(db, group.id, member.id)
    else:
        await require_owner(db, group, member)
    group.dissolved_at = dt.datetime.now(dt.timezone.utc)
    await add_system_message(db, group, member, f"群 {group.name} 已解散")
    await db.commit()
    return {"schema": 1, "dissolved": True}


@router.get("/groups/{group_id}/members")
async def list_members(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = await get_group_or_404(db, group_id)
    rows = (await db.execute(
        select(Member, GroupMember.role)
        .join(GroupMember, GroupMember.member_id == Member.id)
        .where(
            GroupMember.group_id == group.id,
            GroupMember.removed_at.is_(None),
        )
    )).all()
    members = [m for m, _role in rows]
    states = await push_states(db, members, request.app.state.wake_bus)
    result = []
    for m, role in rows:
        state = states.get(m.id, {"reach": "away", "connected": False})
        d = member_dict(m, member, reach=state["reach"], push_connected=state["connected"])
        d["role"] = role
        d["pushReason"] = state.get("reason")
        result.append(d)
    return {"schema": 1, "members": result}


@router.post("/join")
async def join_by_invite(
    body: JoinByInvite,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = await group_from_invite(db, body.invite)
    joined = await join_group(
        db, group, member, limit=request.app.state.settings.members_per_group_limit
    )
    return {
        "schema": 1,
        "joined": joined,
        "group": group_dict(group),
        "note": "已加入，打个招呼吧" if joined else "已在群中",
    }
