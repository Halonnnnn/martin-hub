import datetime as dt
import time

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import check_fence, current_member
from ..errors import AppError, bad_request, forbidden, not_found
from ..models import FriendRequest, Group, GroupMember, Member, Message
from ..schemas import MessageSend, ReadMark
from ..serialize import message_dict
from ..services import (
    get_group_or_404,
    send_message,
)

router = APIRouter()


async def get_db(request: Request):
    async with request.app.state.sessionmaker() as session:
        yield session


@router.get("/inbox")
async def inbox(
    request: Request,
    since: int | None = None,
    wait: int = 0,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    wait = max(0, min(wait, 60))
    deadline = time.monotonic() + wait
    bus = request.app.state.wake_bus
    while True:
        probe = await _probe(db, member)
        if since is None or probe["cursor"] != since or time.monotonic() >= deadline:
            return {"schema": 1, **await _inbox_payload(db, member)}
        # 兜底 5s 重新探针，覆盖多进程部署或在其他实例发生的变更
        await bus.wait_inbox_change(max(0.0, min(5.0, deadline - time.monotonic())))


@router.get("/probe")
async def probe(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    return {"schema": 1, **await _probe(db, member)}


async def _probe(db: AsyncSession, member: Member) -> dict:
    """轻量探针：两条索引查询，不做每群聚合。"""
    cursor = (await db.execute(select(func.max(Message.seq)))).scalar() or 0
    unread = (
        select(1)
        .select_from(Message)
        .join(GroupMember, GroupMember.group_id == Message.group_id)
        .join(Group, Group.id == Message.group_id)
        .where(
            GroupMember.member_id == member.id,
            GroupMember.removed_at.is_(None),
            Group.dissolved_at.is_(None),
            Message.seq > GroupMember.last_read_seq,
            Message.author_id != member.id,
        )
        .exists()
    )
    pending = bool((await db.execute(select(unread))).scalar())
    return {"pending": pending, "cursor": cursor}


async def _inbox_payload(db: AsyncSession, member: Member) -> dict:
    rows = await db.execute(
        select(Group, GroupMember.last_read_seq)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(
            GroupMember.member_id == member.id,
            GroupMember.removed_at.is_(None),
            Group.dissolved_at.is_(None),
        )
    )
    groups_out = []
    global_cursor = 0
    # 一个群可能有多个人类成员：fromYourOwner 只算自己 owner 那个人类成员
    owner_human_rows = await db.execute(
        select(Member.owner_id, Member.id).where(
            Member.human.is_(True), Member.owner_id.is_not(None)
        )
    )
    owner_human_by_owner = {owner_id: mid for owner_id, mid in owner_human_rows.all()}
    my_owner_human_id = (
        owner_human_by_owner.get(member.owner_id)
        if member.owner_id is not None
        else None
    )
    for group, last_read in rows.all():
        unread_rows = (
            await db.execute(
                select(Message)
                .where(
                    Message.group_id == group.id,
                    Message.seq > last_read,
                    Message.author_id != member.id,
                )
                .order_by(Message.seq.asc())
            )
        ).scalars().all()
        from_you_owner = 0
        mentions_you = 0
        replies_you = 0
        recalled_n = 0
        urgent_n = 0
        my_seqs_rows = (
            await db.execute(
                select(Message.seq).where(
                    Message.group_id == group.id, Message.author_id == member.id
                )
            )
        ).scalars().all()
        my_seqs = set(my_seqs_rows)
        for m in unread_rows:
            global_cursor = max(global_cursor, m.seq)
            if my_owner_human_id is not None and m.author_id == my_owner_human_id:
                from_you_owner += 1
            if member.name in (m.mentions or []):
                mentions_you += 1
            if m.reply_to_seq in my_seqs:
                replies_you += 1
            if m.recalled_at is not None:
                recalled_n += 1
            if m.priority == "urgent":
                urgent_n += 1
        d = {
            "groupId": group.public_id,
            "name": group.name,
            "unread": len(unread_rows),
            "urgent": urgent_n,
        }
        if from_you_owner:
            d["fromYourOwner"] = from_you_owner
        if mentions_you:
            d["mentionsYou"] = mentions_you
        if replies_you:
            d["repliesToYou"] = replies_you
        if recalled_n:
            d["recalled"] = recalled_n
        groups_out.append(d)
    max_row = await db.execute(select(func.max(Message.seq)))
    global_cursor = max(global_cursor, max_row.scalar() or 0)
    fr_rows = await db.execute(
        select(FriendRequest)
        .where(FriendRequest.to_member == member.id, FriendRequest.status == "pending")
        .order_by(FriendRequest.id)
    )
    notices = []
    for fr in fr_rows.scalars().all():
        frm = await db.get(Member, fr.from_member)
        notices.append(
            {
                "type": "friend_request",
                "id": fr.id,
                "from": frm.name if frm else str(fr.from_member),
                "note": fr.note,
            }
        )
    return {
        "you": member.name,
        "groups": groups_out,
        "notices": notices,
        "cursor": global_cursor,
    }


@router.post("/groups/{group_id}/messages")
async def post_message(
    group_id: str,
    body: MessageSend,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    await check_fence(request, db, member)
    group = await get_group_or_404(db, group_id)
    result = await send_message(
        db,
        request.app.state.settings,
        request.app.state.cache,
        group,
        member,
        body,
        bus=request.app.state.wake_bus,
        kick=request.app.state.dispatcher.kick,
    )
    return {"schema": 1, **result}


@router.get("/groups/{group_id}/messages")
async def read_messages(
    group_id: str,
    request: Request,
    after: int | None = None,
    before: int | None = None,
    limit: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = await get_group_or_404(db, group_id)
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == member.id,
            GroupMember.removed_at.is_(None),
        )
    )
    gm = row.scalar_one_or_none()
    if gm is None:
        raise forbidden("not a member of this group")

    settings = request.app.state.settings
    page_limit = limit if limit is not None else settings.read_page_limit_default
    if page_limit < 1 or page_limit > 500:
        raise bad_request("limit 取值在 1-500 之间", "invalid_limit")

    page_mode = after is not None or before is not None

    if page_mode:
        q = select(Message).where(Message.group_id == group.id)
        if after is not None:
            q = q.where(Message.seq > after)
        if before is not None:
            q = q.where(Message.seq < before)
        q = q.order_by(Message.seq.asc()).limit(page_limit)
        msgs = (await db.execute(q)).scalars().all()
        return await _read_response(db, msgs, member, gm, mark_read=False, new_cutoff=None)

    # 未读模式
    unread_q = (
        select(Message)
        .where(
            Message.group_id == group.id,
            Message.seq > gm.last_read_seq,
        )
        .order_by(Message.seq.asc())
    )
    unread = (await db.execute(unread_q)).scalars().all()
    max_seq_row = await db.execute(
        select(func.max(Message.seq)).where(Message.group_id == group.id)
    )
    group_max_seq = max_seq_row.scalar() or gm.last_read_seq

    skipped = 0
    delivered = unread
    if len(unread) > page_limit:
        keep_ids: set[int] = set()
        my_seqs = {m.seq for m in unread if m.author_id == member.id}
        for m in unread:
            if member.name in (m.mentions or []) or (
                m.reply_to_seq is not None and m.reply_to_seq in my_seqs
            ):
                keep_ids.add(m.id)
        for m in reversed(unread):
            if len(keep_ids) >= page_limit:
                break
            keep_ids.add(m.id)
        delivered = [m for m in unread if m.id in keep_ids]
        skipped = len(unread) - len(delivered)

    return await _read_response(
        db, delivered, member, gm, mark_read=True, new_cutoff=gm.last_read_seq,
        cursor_override=group_max_seq, skipped=skipped,
    )


async def _read_response(
    db: AsyncSession,
    msgs,
    member: Member,
    gm: GroupMember,
    mark_read: bool,
    new_cutoff: int | None,
    cursor_override: int | None = None,
    skipped: int = 0,
) -> dict:
    author_cache: dict[int, Member] = {}
    out = []
    recalled = []
    for m in msgs:
        a = author_cache.get(m.author_id)
        if a is None:
            a = await db.get(Member, m.author_id)
            author_cache[m.author_id] = a
        is_new = new_cutoff is not None and m.seq > new_cutoff and m.author_id != member.id
        out.append(message_dict(m, a, member, new=is_new))
        if m.recalled_at is not None:
            recalled.append(m.seq)
    max_delivered = msgs[-1].seq if msgs else None
    if mark_read:
        new_watermark = cursor_override if cursor_override is not None else max_delivered
        if new_watermark is not None and new_watermark > gm.last_read_seq:
            gm.last_read_seq = new_watermark
            await db.commit()
    cursor = cursor_override if cursor_override is not None else (max_delivered if max_delivered is not None else gm.last_read_seq)
    return {
        "schema": 1,
        "you": member.name,
        "messages": out,
        "cursor": cursor,
        "skipped": skipped,
        "recalled": recalled,
    }


@router.delete("/groups/{group_id}/messages/{seq}")
async def recall_message(
    group_id: str,
    seq: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    group = await get_group_or_404(db, group_id)
    row = await db.execute(
        select(Message).where(Message.group_id == group.id, Message.seq == seq)
    )
    msg = row.scalar_one_or_none()
    if msg is None:
        raise not_found("message not found")
    if msg.author_id != member.id:
        raise forbidden("只能撤回自己发送的消息")
    if msg.recalled_at is not None:
        return {"schema": 1, "recalled": True, "seq": seq}
    settings = request.app.state.settings
    age = dt.datetime.now(dt.timezone.utc) - msg.created_at
    if age.total_seconds() > settings.recall_window_seconds:
        raise AppError("recall_expired", "已超过 2 分钟撤回时限", 409)
    msg.recalled_at = dt.datetime.now(dt.timezone.utc)
    await db.commit()
    return {"schema": 1, "recalled": True, "seq": seq}


@router.post("/groups/{group_id}/read")
async def mark_read(
    group_id: str,
    body: ReadMark,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    member = await current_member(request, db)
    await check_fence(request, db, member)
    group = await get_group_or_404(db, group_id)
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == member.id,
            GroupMember.removed_at.is_(None),
        )
    )
    gm = row.scalar_one_or_none()
    if gm is None:
        raise forbidden("not a member of this group")
    if body.all:
        max_seq_row = await db.execute(
            select(func.max(Message.seq)).where(Message.group_id == group.id)
        )
        target = max_seq_row.scalar() or gm.last_read_seq
    elif body.seq is not None:
        if body.seq < gm.last_read_seq:
            raise bad_request("seq 不能小于当前已读位置", "invalid_read_seq")
        target = body.seq
    else:
        raise bad_request("需要 seq 或 all=true", "invalid_read_seq")
    gm.last_read_seq = target
    await db.commit()
    return {"schema": 1, "lastReadSeq": gm.last_read_seq}
