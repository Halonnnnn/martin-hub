import datetime as dt
import json
import re

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .errors import AppError, bad_request, forbidden, not_found
from .models import (
    Checkin,
    Group,
    GroupMember,
    Member,
    Message,
    TempGrant,
    WakeConfig,
    WakeQueue,
)
from .security import gen_invite_code, sha256_hex
from .serialize import message_dict
from .timeutil import as_utc, now_utc
from . import wake

ALL_AGENTS = "all agents"

AT_NAME_RE = re.compile(r"@([^\s@，。；,;!?]+(?:\s[^\s@，。；,;!?]+)*)")

TELL_OWNER_TEXT = (
    "已转达。提醒你的用户：用户也可以在 Martin Web 页面直接在本群发言。"
    "本提示每个群只出现一次。"
)
SEND_NOTE_TEXT = "用收件箱或 GET /groups/{id}/messages?after=<seq> 查看回复。"


def request_fingerprint(
    *,
    group_id: int,
    text: str,
    mentions: list[str] | None,
    reply_to: int | None,
    priority: str,
    relay: bool,
) -> str:
    normalized = sorted(
        {m.strip().removeprefix("@") for m in (mentions or []) if m and m.strip()}
    )
    body = json.dumps(
        {
            "group": group_id,
            "text": text,
            "mentions": normalized,
            "replyTo": reply_to,
            "priority": priority,
            "relay": relay,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_hex(body)


QUESTION_IDS = "ABCD"


def render_question(q) -> tuple[str, dict]:
    """结构是权威：正文由结构生成；payload 存结构供客户端渲染。"""
    options = [
        {"id": QUESTION_IDS[i], "label": o.label, "consequence": o.consequence}
        for i, o in enumerate(q.options)
    ]
    lines = [q.prompt, ""]
    for o in options:
        line = f"{o['id']}. {o['label']}"
        if o["consequence"]:
            line += f"（{o['consequence']}）"
        lines.append(line)
    recommended = None
    if q.recommendedIndex is not None:
        rid = QUESTION_IDS[q.recommendedIndex]
        recommended = {"id": rid, "reason": q.recommendedReason}
        lines.append("")
        lines.append(
            f"建议选 {rid}" + (f"：{q.recommendedReason}" if q.recommendedReason else "")
        )
        lines.append("回复选项字母（如 A），或直接说你的想法。")
    return "\n".join(lines), {
        "prompt": q.prompt,
        "options": options,
        "recommended": recommended,
    }


async def active_membership(db: AsyncSession, group_id: int, member_id: int) -> GroupMember:
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.member_id == member_id,
            GroupMember.removed_at.is_(None),
        )
    )
    gm = row.scalar_one_or_none()
    if gm is None:
        raise forbidden("not a member of this group")
    return gm


async def ensure_group_capacity(
    db: AsyncSession, group_id: int, limit: int | None
) -> None:
    if limit is None:
        return
    active = (
        await db.execute(
            select(func.count())
            .select_from(GroupMember)
            .where(
                GroupMember.group_id == group_id,
                GroupMember.removed_at.is_(None),
            )
        )
    ).scalar() or 0
    if active >= limit:
        raise AppError("group_full", f"群成员已达上限（{limit}）", 409)


async def get_group_or_404(db: AsyncSession, public_id: str) -> Group:
    group = (
        await db.execute(select(Group).where(Group.public_id == public_id))
    ).scalar_one_or_none()
    if group is None or group.dissolved_at is not None:
        raise not_found("group not found or dissolved")
    return group


async def get_member_or_404(db: AsyncSession, public_id: str) -> Member:
    member = (
        await db.execute(select(Member).where(Member.public_id == public_id))
    ).scalar_one_or_none()
    if member is None:
        raise not_found("目标成员不存在")
    return member


# ---------------------------------------------------------------- 临时授权

TEMP_REDACT_PATTERNS = (
    (re.compile(r"martin_[A-Za-z0-9_\-]{8,}"), "[已脱敏]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [已脱敏]"),
)


def temp_grant_can_renew(
    grant: TempGrant, now: dt.datetime | None = None
) -> bool:
    now = now or now_utc()
    return grant.revoked_at is None and now < as_utc(grant.max_access_at)


def temp_grant_status(grant: TempGrant, now: dt.datetime | None = None) -> str:
    """授权状态；优先级 revoked > closed > expired > unredeemed > exhausted > active。

    截止时间一律按 UTC 比较：库里即使读到 naive 值（老库 timestamp 列），
    也不会因为时区差被误判成过期。
    """
    now = now or now_utc()
    if grant.revoked_at is not None:
        return "revoked"
    if grant.closed_at is not None:
        return "closed"
    if as_utc(grant.expires_at) <= now:
        return "expired"
    if grant.redeemed_at is None:
        return "unredeemed"
    if grant.messages_used >= grant.max_messages:
        return "exhausted"
    return "active"


async def get_temp_grant_for_group(
    db: AsyncSession, group_id: int
) -> TempGrant | None:
    row = await db.execute(select(TempGrant).where(TempGrant.group_id == group_id))
    return row.scalar_one_or_none()


async def expire_temp_grant(
    db: AsyncSession, grant: TempGrant, *, dissolve: bool = True
) -> Group | None:
    """活动窗口到期：清 token，可选 dissolve 群，但不写 closed_at。

    访客到期时用 dissolve=False：清掉访客 token，但把群留着，让已经开始的 agent
    回复有宽限期可以落库；宽限期结束后 agent 再发才会 dissolve。
    这样 owner 仍能在 max_access_at 之前从历史里续验证码，历史消息原样保留。
    """
    now = now_utc()
    grant.session_token_hash = None
    if not dissolve:
        return None
    group = await db.get(Group, grant.group_id) if grant.group_id else None
    if group is not None and group.dissolved_at is None:
        group.dissolved_at = now
        await db.execute(
            delete(WakeQueue).where(
                WakeQueue.group_id == group.id, WakeQueue.status == "pending"
            )
        )
    return group


async def close_temp_grant(
    db: AsyncSession, grant: TempGrant, reason: str
) -> Group | None:
    """结束授权：失效访客 token、dissolve 临时群、清掉待派发唤醒。

    不 commit，由调用方决定事务边界；群上的 system 消息由调用方补充。
    """
    now = now_utc()
    if reason == "revoked":
        grant.revoked_at = now
    else:
        grant.closed_at = now
    grant.close_reason = reason
    grant.session_token_hash = None
    group = await db.get(Group, grant.group_id) if grant.group_id else None
    if group is not None and group.dissolved_at is None:
        group.dissolved_at = now
        await db.execute(
            delete(WakeQueue).where(
                WakeQueue.group_id == group.id, WakeQueue.status == "pending"
            )
        )
    return group


def reject_temp_group_ops(group: Group) -> None:
    """临时群的成员/元数据/生命周期只能由 TempGrant 接口管理。"""
    if group.kind == "temp":
        raise AppError(
            "temp_no_group_ops",
            "临时会话由授权生命周期管理，不能直接加入、邀请、退出或解散",
            403,
        )


async def ensure_temp_open(
    db: AsyncSession,
    group: Group,
    *,
    allow_after_expiry: bool = False,
    grace_seconds: int = 0,
) -> TempGrant | None:
    """临时群的消息守卫：结束/到期后拒绝写入，并尽量把过期群收尾。

    allow_after_expiry 只给 agent 的最终回复用：活动窗口到期后，agent 仍可在
    grace_seconds 内把已经开始的一轮回复写完；访客始终没有宽限。
    """
    if group.kind != "temp":
        return None
    grant = await get_temp_grant_for_group(db, group.id)
    if grant is None:
        raise AppError("temp_closed", "临时会话不存在或已结束", 403)
    status = temp_grant_status(grant)
    if status in ("revoked", "closed"):
        if group.dissolved_at is None:
            group.dissolved_at = now_utc()
            await db.commit()
        raise AppError("temp_closed", "临时会话已结束", 403)
    if status in ("expired", "unredeemed"):
        now = now_utc()
        if status == "unredeemed":
            # 走到这里的一定是续期待核销（按群反查的授权必有 group_id）：
            # owner 已续期、窗口与条数重置、群保留，访客还没输入新码。
            # 授权是活的，agent 的在途回复照常落库，访客重新进入后就能看到。
            return grant
        if (
            allow_after_expiry
            and now < as_utc(grant.expires_at) + dt.timedelta(seconds=max(0, grace_seconds))
        ):
            return grant
        await expire_temp_grant(db, grant)
        await db.commit()
        raise AppError("temp_expired", "临时会话已到期", 403)
    return grant


def redact_temp_text(text: str, settings: Settings) -> str:
    """最小脱敏：只拦常见 token 形态，防手滑，不作为安全边界。"""
    if not settings.temp_redact_enabled:
        return text
    for pattern, replacement in TEMP_REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


async def parse_mentions(
    db: AsyncSession,
    group_id: int,
    author: Member,
    text: str,
    mentions: list[str],
    scan_text: bool = True,
) -> tuple[list[int], bool, str | None]:
    rows = await db.execute(
        select(Member).join(GroupMember, GroupMember.member_id == Member.id).where(
            GroupMember.group_id == group_id,
            GroupMember.removed_at.is_(None),
        )
    )
    members = rows.scalars().all()
    # 用户（human）不是可提起对象：只有 agent 会被 @ 通知，用户由 Web 消息流自行查看
    agents = [m for m in members if not m.human]
    human_names = {m.name for m in members if m.human}
    by_name = {m.name: m for m in agents}
    by_id = {m.public_id: m for m in agents}

    all_agents = False
    mentioned: dict[int, Member] = {}
    matched_names: set[str] = set()
    human_tokens: list[str] = []

    for entry in mentions:
        entry_clean = entry.removeprefix("@").strip()
        if entry_clean == ALL_AGENTS or entry == ALL_AGENTS:
            all_agents = True
            continue
        if entry_clean in human_names:
            human_tokens.append(entry_clean)
            continue
        m = by_name.get(entry_clean) or by_name.get(entry) or by_id.get(entry_clean)
        if m is not None:
            mentioned[m.id] = m
            matched_names.add(m.name)

    for name, m in by_name.items():
        if scan_text and f"@{name}" in text:
            mentioned[m.id] = m
            matched_names.add(name)

    hint_parts = []
    for tok in AT_NAME_RE.findall(text) if scan_text else []:
        if tok == ALL_AGENTS:
            continue
        if any(tok.startswith(n) for n in matched_names):
            continue
        if tok in human_names:
            human_tokens.append(tok)
            continue
        hint_parts.append(tok)
    hints = []
    if human_tokens:
        names = list(dict.fromkeys(human_tokens))
        hints.append(
            "、".join(f"@{t}" for t in names)
            + " 是用户，不能被 @，不会通知任何人"
        )
    if hint_parts:
        hints.append(
            "、".join(f"@{t}" for t in hint_parts) + " 未匹配到成员，不会通知任何人"
        )
    mention_hint = "；".join(hints) if hints else None

    if all_agents:
        group = await db.get(Group, group_id)
        owner_member = await db.get(Member, group.owner_member_id)
        is_owner_human = author.human and owner_member is not None and (
            author.id == owner_member.id
            or (
                owner_member.owner_id is not None
                and author.owner_id == owner_member.owner_id
            )
        )
        if not is_owner_human:
            raise forbidden("all agents 提及只能由群主的人类成员发送")

    return list(mentioned.keys()), all_agents, mention_hint


async def send_message(
    db: AsyncSession,
    settings: Settings,
    cache,
    group: Group,
    author: Member,
    payload,
    bus=None,
    kick=None,
    allow_text_mentions: bool = True,
) -> dict:
    if len(payload.text) > settings.message_max_length:
        raise AppError("text_too_long", "消息超过长度上限", 422)
    if group.archived_at is not None:
        raise AppError("group_archived", "群已归档，只能查看历史消息", 403)

    gm = await active_membership(db, group.id, author.id)
    await ensure_temp_open(
        db,
        group,
        allow_after_expiry=not author.human,
        grace_seconds=settings.temp_agent_reply_grace_seconds,
    )

    bucket = int(dt.datetime.now(dt.timezone.utc).timestamp() // 3600)
    count = await cache.incr(f"martin:gr:{group.id}:{bucket}", 3600)
    if count > settings.group_hourly_message_limit:
        raise AppError("rate_limited", "本群消息达到每小时上限", 429)

    if payload.replyTo is not None:
        r = await db.execute(
            select(Message).where(
                Message.group_id == group.id, Message.seq == payload.replyTo
            )
        )
        if r.scalar_one_or_none() is None:
            raise bad_request("replyTo 不存在", "invalid_reply")

    mentioned_ids, all_agents, mention_hint = await parse_mentions(
        db,
        group.id,
        author,
        payload.text,
        payload.mentions,
        scan_text=allow_text_mentions,
    )

    priority = getattr(payload, "priority", "normal") or "normal"
    request_hash = request_fingerprint(
        group_id=group.id,
        text=payload.text,
        mentions=payload.mentions,
        reply_to=payload.replyTo,
        priority=priority,
        relay=bool(payload.relay),
    )

    # 幂等命中先于"先读后发"门禁：重试不应被期间到达的新消息打断
    if payload.opKey:
        existing = (
            await db.execute(
                select(Message).where(
                    Message.author_id == author.id, Message.op_key == payload.opKey
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_hash is not None and existing.request_hash != request_hash:
                raise AppError(
                    "idempotency_conflict", "同一个 opKey 已用于不同内容", 409
                )
            return {
                "seq": existing.seq,
                "mentioned": existing.mentions or [],
                "mentionHint": None,
                "tellOwner": None,
                "cursor": existing.seq,
                "note": SEND_NOTE_TEXT.format(id=group.id),
                "idempotent": True,
                "priority": existing.priority,
                "push": {"agents": 0, "degraded": []},
                "pushHint": None,
                "urgentDowngraded": None,
            }

    # 群行锁串行化 seq 分配，锁后再取 max 保证并发下不重号
    await db.execute(select(Group.id).where(Group.id == group.id).with_for_update())
    max_seq_row = await db.execute(
        select(func.max(Message.seq)).where(Message.group_id == group.id)
    )
    max_seq = max_seq_row.scalar() or 0

    if not payload.ignoreNew:
        others_new = (
            await db.execute(
                select(Message)
                .where(
                    Message.group_id == group.id,
                    Message.seq > gm.last_read_seq,
                    Message.author_id != author.id,
                    Message.type == "text",
                )
                .order_by(Message.seq.asc())
            )
        ).scalars().all()
        if others_new:
            gm.last_read_seq = max_seq
            await db.commit()
            raise AppError(
                "new_messages",
                "发送前有新消息，请先阅读",
                409,
                payload={
                    "messages": [
                        message_dict(m, await db.get(Member, m.author_id), author)
                        for m in others_new
                    ]
                },
            )

    priority = getattr(payload, "priority", "normal") or "normal"
    urgent_downgraded = None
    if priority == "urgent":
        now = dt.datetime.now(dt.timezone.utc)
        group_count = await cache.incr(
            f"martin:urgent:g:{group.id}:{now:%Y%m%d%H}", 3600
        )
        sender_count = await cache.incr(
            f"martin:urgent:u:{author.id}:{now:%Y%m%d}", 86400
        )
        if (
            group_count > settings.urgent_group_hourly_limit
            or sender_count > settings.urgent_sender_daily_limit
        ):
            priority = "normal"
            urgent_downgraded = "urgent_limited"

    msg_type = "text"
    msg_text = payload.text
    question_payload = None
    if payload.question is not None:
        if payload.question.recommendedReason and payload.question.recommendedIndex is None:
            raise bad_request("recommendedReason 需要配合 recommendedIndex", "invalid_question")
        if (
            payload.question.recommendedIndex is not None
            and payload.question.recommendedIndex >= len(payload.question.options)
        ):
            raise bad_request("recommendedIndex 超出选项范围", "invalid_question")
        msg_type = "question"
        msg_text, question_payload = render_question(payload.question)

    if group.kind == "temp" and not author.human:
        # 访客页面是纯文本；不给结构化的 question payload，避免正文之外再泄露一份。
        if msg_type == "question":
            question_payload = None
        msg_text = redact_temp_text(msg_text, settings)

    next_seq = max_seq + 1
    msg = Message(
        group_id=group.id,
        seq=next_seq,
        author_id=author.id,
        type=msg_type,
        priority=priority,
        text=msg_text,
        mentions=[m.name for m in (await _members_by_ids(db, mentioned_ids))]
        + (["all agents"] if all_agents else []),
        reply_to_seq=payload.replyTo,
        relay=payload.relay,
        op_key=payload.opKey,
        request_hash=request_hash,
        payload=question_payload,
    )
    db.add(msg)

    told_owner = None
    if payload.relay and gm.told_owner_at is None:
        gm.told_owner_at = dt.datetime.now(dt.timezone.utc)
        told_owner = TELL_OWNER_TEXT

    replied_agent_id = None
    if payload.replyTo is not None:
        r = await db.execute(
            select(Message).where(
                Message.group_id == group.id, Message.seq == payload.replyTo
            )
        )
        target = r.scalar_one_or_none()
        if target is not None:
            t_author = await db.get(Member, target.author_id)
            if t_author is not None and not t_author.human:
                replied_agent_id = t_author.id

    targets = await wake.wake_targets(
        db, group, author, mentioned_ids, replied_agent_id, all_agents
    )
    target_members = await _members_by_ids(db, list(targets))
    states = await push_states(db, target_members, bus)
    degraded = []
    pushable = 0
    for m in target_members:
        state = states[m.id]
        if state["reach"] == "wake":
            pushable += 1
            continue
        degraded.append(
            {
                "memberId": m.public_id,
                "name": m.name,
                "reach": state["reach"],
                "reason": state["reason"],
            }
        )

    # 队列是待唤醒事实的唯一来源：目标一律入队，能否投递由派发器判定
    await wake.enqueue_for_message(
        db,
        group,
        author,
        mentioned_ids,
        replied_agent_id,
        all_agents,
        urgent=(priority == "urgent"),
    )

    await db.commit()
    await db.refresh(msg)
    if bus is not None:
        bus.notify_inbox()
    if kick is not None:
        kick()

    push_hint = (
        "；".join(
            f"{d['name']} {PUSH_REASON_TEXT[d['reason']]}" for d in degraded
        )
        or None
    )
    mentioned_names = msg.mentions or []
    return {
        "seq": msg.seq,
        "mentioned": mentioned_names,
        "mentionHint": mention_hint,
        "tellOwner": told_owner,
        "cursor": msg.seq,
        "note": SEND_NOTE_TEXT.format(id=group.id),
        "message": message_dict(msg, author, author, new=False),
        "priority": msg.priority,
        "push": {"agents": pushable, "degraded": degraded},
        "pushHint": push_hint,
        "urgentDowngraded": urgent_downgraded,
    }


async def _members_by_ids(db: AsyncSession, ids: list[int]) -> list[Member]:
    if not ids:
        return []
    rows = await db.execute(select(Member).where(Member.id.in_(ids)))
    return list(rows.scalars().all())


PUSH_REASON_TEXT = {
    "no_channel": "未接入推送，将在下次检查时看到",
    "muted": "已开启免打扰，不会即时叫醒",
    "disabled": "推送已停用",
    "listener_offline": "推送通道未连接，将在恢复后送达",
}


async def push_states(db: AsyncSession, members: list[Member], bus=None) -> dict[int, dict]:
    """每个成员的推送可达性：reach 四态 + 连接态 + 不可推送原因。"""
    result: dict[int, dict] = {}
    for m in members:
        wc = await db.get(WakeConfig, m.id)
        ck = await db.get(Checkin, m.id)
        connected = bool(
            bus is not None and wc is not None and wc.kind == "listener" and bus.is_connected(m.id)
        )
        if wc is None:
            reach, reason = ("checkin" if ck is not None else "away"), "no_channel"
        elif wc.muted:
            reach, reason = "muted", "muted"
        elif not wc.enabled:
            reach, reason = "away", "disabled"
        elif wc.kind == "listener" and not connected:
            reach, reason = "away", "listener_offline"
        else:
            reach, reason = "wake", None
        result[m.id] = {"reach": reach, "connected": connected, "reason": reason}
    return result


async def reach_for_members(db: AsyncSession, members: list[Member], bus=None) -> dict[int, str]:
    states = await push_states(db, members, bus)
    return {mid: s["reach"] for mid, s in states.items()}


async def get_or_create_direct_group(db: AsyncSession, member: Member, target: Member) -> Group:
    sub_a = select(GroupMember.group_id).where(
        GroupMember.member_id == member.id, GroupMember.removed_at.is_(None)
    )
    sub_b = select(GroupMember.group_id).where(
        GroupMember.member_id == target.id, GroupMember.removed_at.is_(None)
    )
    row = await db.execute(
        select(Group).where(
            Group.kind == "direct",
            Group.dissolved_at.is_(None),
            Group.id.in_(sub_a),
            Group.id.in_(sub_b),
        )
    )
    group = row.scalars().first()
    if group is not None:
        return group
    group = Group(
        name=f"{member.name} ↔ {target.name}"[:100],
        kind="direct",
        visibility="link",
        invite_code=gen_invite_code(),
        owner_member_id=member.id,
    )
    db.add(group)
    await db.flush()
    db.add(GroupMember(group_id=group.id, member_id=member.id, role="owner"))
    db.add(GroupMember(group_id=group.id, member_id=target.id, role="member"))
    await db.commit()
    await db.refresh(group)
    return group
