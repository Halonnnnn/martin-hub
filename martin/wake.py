import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Group, GroupMember, Member, WakeConfig, WakeQueue

WAKE_REASONS = {
    "mention",
    "reply",
    "owner_post",
    "guest_post",
    "all_agents",
    "review_request",
    "friend_request_not_wake",
}


def _trigger_allowed(
    author: Member,
    target: Member,
    cfg: WakeConfig | None,
    temp_delegated: bool = False,
) -> bool:
    """触发范围与群成员资格解耦；缺省 any_member 与历史行为一致。

    temp 群里的访客是 owner 显式发码授权的临时代表，因此不受 allowFrom 限制；
    否则 owner 设了 owner-only 反而叫不醒，授权等于没生效。
    """
    if temp_delegated:
        return True
    if cfg is None or cfg.allow_from == "any_member":
        return True
    if cfg.allow_from == "owner":
        return bool(
            author.human
            and target.owner_id is not None
            and author.owner_id == target.owner_id
        )
    return author.name in (cfg.allow_list or [])


async def group_agent_ids(db: AsyncSession, group_id: int) -> list[int]:
    rows = await db.execute(
        select(Member.id)
        .join(GroupMember, GroupMember.member_id == Member.id)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.removed_at.is_(None),
            Member.human.is_(False),
        )
    )
    return [r[0] for r in rows.all()]


async def enqueue_wake(
    db: AsyncSession, member_id: int, group_id: int, reason: str, urgent: bool = False
) -> None:
    if reason not in WAKE_REASONS or reason == "friend_request_not_wake":
        return
    db.add(
        WakeQueue(
            member_id=member_id,
            group_id=group_id,
            reason=reason,
            urgent=urgent,
            status="pending",
            next_at=dt.datetime.now(dt.timezone.utc),
        )
    )


async def wake_targets(
    db: AsyncSession,
    group: Group,
    author: Member,
    mentioned_agent_ids: list[int],
    replied_agent_id: int | None,
    all_agents: bool,
) -> dict[int, str]:
    targets: dict[int, str] = {}
    for mid in mentioned_agent_ids:
        targets[mid] = "mention"
    if replied_agent_id is not None and replied_agent_id not in targets:
        targets[replied_agent_id] = "reply"
    if author.human:
        reason = "guest_post" if author.platform == "guest" else "owner_post"
        for mid in await group_agent_ids(db, group.id):
            targets[mid] = reason
    elif all_agents:
        for mid in await group_agent_ids(db, group.id):
            targets.setdefault(mid, "all_agents")
    targets.pop(author.id, None)

    temp_delegated = group.kind == "temp" and author.platform == "guest"
    allowed: dict[int, str] = {}
    for mid, reason in targets.items():
        target = await db.get(Member, mid)
        if target is None:
            continue
        cfg = await db.get(WakeConfig, mid)
        if _trigger_allowed(author, target, cfg, temp_delegated):
            allowed[mid] = reason
    return allowed


async def enqueue_for_message(
    db: AsyncSession,
    group: Group,
    author: Member,
    mentioned_agent_ids: list[int],
    replied_agent_id: int | None,
    all_agents: bool,
    urgent: bool = False,
) -> None:
    targets = await wake_targets(
        db, group, author, mentioned_agent_ids, replied_agent_id, all_agents
    )
    for mid, reason in targets.items():
        await enqueue_wake(db, mid, group.id, reason, urgent=urgent)
