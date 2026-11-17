from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_member
from ..errors import AppError, bad_request, not_found
from ..models import FriendRequest, Friendship, Member
from ..schemas import ChatCreate, FriendRequestAction, FriendRequestCreate
from ..serialize import group_dict, member_dict
from ..services import get_or_create_direct_group, reach_for_members

router = APIRouter()


async def get_db(request: Request):
    async with request.app.state.sessionmaker() as session:
        yield session


def pair_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


async def get_friendship(db: AsyncSession, a: int, b: int) -> Friendship | None:
    lo, hi = pair_key(a, b)
    row = await db.execute(
        select(Friendship).where(Friendship.member_a == lo, Friendship.member_b == hi)
    )
    return row.scalar_one_or_none()


async def are_friends(db: AsyncSession, a: int, b: int) -> bool:
    return await get_friendship(db, a, b) is not None


async def list_friend_members(db: AsyncSession, member: Member) -> list[Member]:
    """好友只连接用户：同 owner 的 agent 不是好友，自己的 agent 在「接入 Agent」页管理。"""
    rows = (
        await db.execute(
            select(Friendship).where(
                or_(Friendship.member_a == member.id, Friendship.member_b == member.id)
            )
        )
    ).scalars().all()
    other_ids = [f.member_b if f.member_a == member.id else f.member_a for f in rows]
    if not other_ids:
        return []
    members = (
        await db.execute(
            select(Member).where(Member.id.in_(other_ids), Member.human.is_(True))
        )
    ).scalars().all()
    return list(members)


async def pending_requests(db: AsyncSession, member: Member) -> list[FriendRequest]:
    # 好友请求只属于用户；agent 的收件箱不再出现好友请求
    if not member.human:
        return []
    rows = await db.execute(
        select(FriendRequest)
        .join(Member, Member.id == FriendRequest.from_member)
        .where(
            FriendRequest.to_member == member.id,
            FriendRequest.status == "pending",
            Member.human.is_(True),
        )
        .order_by(FriendRequest.id)
    )
    return list(rows.scalars().all())


async def send_friend_request(
    db: AsyncSession, from_member: Member, to_id: str, note: str
) -> FriendRequest:
    if not from_member.human:
        raise AppError("friends_are_people", "好友只连接用户，agent 请由 owner 在群内协作", 403)
    key = to_id.strip()
    target = (
        await db.execute(select(Member).where(Member.public_id == key))
    ).scalar_one_or_none()
    if target is None:
        target = (
            await db.execute(select(Member).where(Member.name == key))
        ).scalar_one_or_none()
    if target is None:
        raise not_found("目标用户不存在")
    if not target.human:
        raise AppError("friends_are_people", "只能添加用户为好友，agent 请在「接入 Agent」中管理", 403)
    if target.id == from_member.id:
        raise bad_request("不能加自己为好友", "self_friend")
    if await are_friends(db, from_member.id, target.id):
        raise bad_request("已经是好友", "already_friends")
    dup = await db.execute(
        select(FriendRequest).where(
            FriendRequest.status == "pending",
            or_(
                (FriendRequest.from_member == from_member.id) & (FriendRequest.to_member == target.id),
                (FriendRequest.from_member == target.id) & (FriendRequest.to_member == from_member.id),
            ),
        )
    )
    if dup.scalar_one_or_none() is not None:
        raise bad_request("已有待处理的好友请求", "request_pending")
    fr = FriendRequest(
        from_member=from_member.id, to_member=target.id, note=note, status="pending"
    )
    db.add(fr)
    await db.commit()
    await db.refresh(fr)
    return fr


async def respond_request(
    db: AsyncSession, member: Member, request_id: int, action: str
) -> dict:
    row = await db.execute(
        select(FriendRequest).where(
            FriendRequest.id == request_id, FriendRequest.to_member == member.id
        )
    )
    fr = row.scalar_one_or_none()
    if fr is None:
        raise not_found("好友请求不存在")
    if fr.status != "pending":
        raise bad_request("请求已处理", "request_resolved")
    if action == "accept":
        if not await are_friends(db, fr.from_member, fr.to_member):
            lo, hi = pair_key(fr.from_member, fr.to_member)
            db.add(Friendship(member_a=lo, member_b=hi))
        fr.status = "accepted"
        await db.commit()
        other = await db.get(Member, fr.from_member)
        return {"ok": True, "status": "accepted", "friend": member_dict(other, member) if other else None}
    fr.status = "ignored"
    await db.commit()
    return {"ok": True, "status": "ignored"}


async def remove_friend(db: AsyncSession, member: Member, other_id: str) -> None:
    other = (
        await db.execute(select(Member).where(Member.public_id == other_id))
    ).scalar_one_or_none()
    if other is None:
        raise not_found("不是好友")
    fs = await get_friendship(db, member.id, other.id)
    if fs is None:
        raise not_found("不是好友")
    await db.delete(fs)
    await db.commit()


async def can_chat(db: AsyncSession, member: Member, target: Member) -> bool:
    """私聊只有两种：用户↔自己的 agent，用户↔用户好友。跨用户 agent 私聊禁止。"""
    if (
        member.owner_id is not None
        and target.owner_id is not None
        and member.owner_id == target.owner_id
    ):
        return True
    if not member.human or not target.human:
        return False
    return await are_friends(db, member.id, target.id)


async def request_dict(db: AsyncSession, fr: FriendRequest, viewer: Member) -> dict:
    other = await db.get(Member, fr.from_member)
    return {
        "id": fr.id,
        "from": member_dict(other, viewer) if other else {"id": None},
        "note": fr.note,
        "status": fr.status,
        "createdAt": fr.created_at.isoformat() if fr.created_at else None,
    }


async def open_chat(db: AsyncSession, member: Member, target_id: str) -> dict:
    target = (
        await db.execute(select(Member).where(Member.public_id == target_id))
    ).scalar_one_or_none()
    if target is None:
        raise not_found("目标成员不存在")
    if target.id == member.id:
        raise bad_request("不能和自己私聊", "self_chat")
    if not await can_chat(db, member, target):
        raise AppError(
            "chat_not_allowed",
            "私聊只能发生在用户之间（需先加好友），或你和自己的 agent 之间",
            403,
        )
    group = await get_or_create_direct_group(db, member, target)
    return {"group": group_dict(group), "peer": member_dict(target, member)}


@router.get("/friends")
async def list_friends(request: Request, db: AsyncSession = Depends(get_db)):
    member = await current_member(request, db)
    friends = await list_friend_members(db, member)
    reaches = await reach_for_members(db, friends, request.app.state.wake_bus)
    return {
        "schema": 1,
        "friends": [member_dict(m, member, reach=reaches.get(m.id, "away")) for m in friends],
    }


@router.post("/friends")
async def create_friend_request(
    body: FriendRequestCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    member = await current_member(request, db)
    fr = await send_friend_request(db, member, body.to, body.note)
    return {"schema": 1, "ok": True, "request": await request_dict(db, fr, member)}


@router.get("/friends/requests")
async def list_requests(request: Request, db: AsyncSession = Depends(get_db)):
    member = await current_member(request, db)
    reqs = await pending_requests(db, member)
    return {"schema": 1, "requests": [await request_dict(db, fr, member) for fr in reqs]}


@router.post("/friends/requests/{request_id}")
async def act_on_request(
    request_id: int,
    body: FriendRequestAction,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    result = await respond_request(db, member, request_id, body.action)
    return {"schema": 1, **result}


@router.delete("/friends/{member_id}")
async def delete_friend(
    member_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    member = await current_member(request, db)
    await remove_friend(db, member, member_id)
    return {"schema": 1, "ok": True}


@router.post("/chats")
async def create_chat(
    body: ChatCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    member = await current_member(request, db)
    result = await open_chat(db, member, body.with_)
    return {"schema": 1, **result}