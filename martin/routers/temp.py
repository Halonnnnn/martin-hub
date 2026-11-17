import datetime as dt
import math
import secrets

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_web_owner_id
from ..errors import AppError, bad_request, forbidden, not_found, unauthorized
from ..models import Group, GroupMember, Member, Message, TempGrant
from ..schemas import MessageSend, TempGrantCreate, TempMessageSend, TempRedeem
from ..security import (
    CODE_ALPHABET,
    gen_invite_code,
    gen_temp_code,
    gen_token,
    normalize_temp_code,
    sha256_hex,
)
from ..serialize import message_dict
from ..timeutil import as_utc, now_utc
from ..services import (
    close_temp_grant,
    expire_temp_grant,
    get_member_or_404,
    push_states,
    send_message,
    temp_grant_can_renew,
    temp_grant_status,
)
from .groups import add_system_message
from .web import owner_and_human

router = APIRouter()


async def get_db(request: Request):
    async with request.app.state.sessionmaker() as session:
        yield session


def _now() -> dt.datetime:
    return now_utc()


def _iso(value: dt.datetime | None) -> str | None:
    # 服务端只给带时区的绝对值，倒计时与截止时间都由浏览器按本地时区渲染
    return as_utc(value).isoformat() if value is not None else None


def _minutes_left(value: dt.datetime, now: dt.datetime) -> int:
    """剩余分钟数，向上取整：5 分钟窗口刚打开时不显示「约剩 4 分钟」。"""
    return max(0, math.ceil((value - now).total_seconds() / 60))


def _grant_dict(
    grant: TempGrant, agent: Member | None, now: dt.datetime | None = None
) -> dict:
    now = now or _now()
    return {
        "id": grant.public_id,
        "agent": {"id": agent.public_id, "name": agent.name} if agent else None,
        "label": grant.label,
        "status": temp_grant_status(grant, now),
        # 浏览器据此纠正本机时钟偏差；倒计时和截止时间都以它为准
        "serverNow": _iso(now),
        "accessMinutes": grant.access_seconds // 60,
        "maxAccessMinutes": grant.max_access_seconds // 60,
        "maxAccessAt": _iso(grant.max_access_at),
        "maxRemainingSeconds": max(
            0, int((grant.max_access_at - now).total_seconds())
        ),
        "canRenew": temp_grant_can_renew(grant, now),
        "maxMessages": grant.max_messages,
        "messagesUsed": grant.messages_used,
        "remainingSeconds": max(
            0, int((grant.expires_at - now).total_seconds())
        ),
        "remainingMessages": max(0, grant.max_messages - grant.messages_used),
        "createdAt": _iso(grant.created_at),
        "expiresAt": _iso(grant.expires_at),
        "redeemedAt": _iso(grant.redeemed_at),
        "closedAt": _iso(grant.closed_at or grant.revoked_at),
        "closeReason": grant.close_reason,
        "hasTranscript": grant.group_id is not None,
    }


def _temp_state(
    grant: TempGrant,
    agent: Member,
    group: Group,
    reach: str,
    now: dt.datetime | None = None,
) -> dict:
    now = now or _now()
    access_remaining = max(0, int((grant.expires_at - now).total_seconds()))
    max_remaining = max(0, int((grant.max_access_at - now).total_seconds()))
    return {
        "grantId": grant.public_id,
        "groupId": group.public_id,
        "agent": {"id": agent.public_id, "name": agent.name, "reach": reach},
        "status": temp_grant_status(grant, now),
        # 浏览器据此纠正本机时钟偏差，避免本机时间与服务器时间不同导致提前结束
        "serverNow": _iso(now),
        "expiresAt": _iso(grant.expires_at),
        "maxAccessAt": _iso(grant.max_access_at),
        # 访客页面只展示这个倒计时；它必须是两个硬上限里更早的那个
        "remainingSeconds": min(access_remaining, max_remaining),
        "accessRemainingSeconds": access_remaining,
        "maxRemainingSeconds": max_remaining,
        "limitReason": "max_access" if max_remaining <= access_remaining else "access",
        "maxMessages": grant.max_messages,
        "messagesUsed": grant.messages_used,
        "remainingMessages": max(0, grant.max_messages - grant.messages_used),
        "closeReason": grant.close_reason,
    }


def _share_text(request: Request, grant: TempGrant, code: str) -> str:
    origin = str(request.base_url).rstrip("/")
    return (
        f"打开 {origin}/t/{grant.public_id} ，输入临时验证码 {code}"
        f"（{grant.access_seconds // 60} 分钟内核销有效，核销后从进入时起算；"
        "一次性，刷新页面后需要重新生成）"
    )


async def _owner_grant(db: AsyncSession, owner_id: int, public_id: str) -> TempGrant:
    grant = (
        await db.execute(
            select(TempGrant).where(
                TempGrant.public_id == public_id, TempGrant.owner_id == owner_id
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise not_found("临时授权不存在")
    return grant


async def _guest_name(db: AsyncSession, label: str | None) -> str:
    # 标签来自 owner/访客：去掉换行/控制空白，避免污染 system 消息与成员名
    base = " ".join((label or "").split())[:20]
    if base:
        base = f"访客·{base}"[:100]
    else:
        base = "访客·" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
    for _ in range(5):
        exists = (
            await db.execute(select(Member.id).where(Member.name == base))
        ).scalar_one_or_none()
        if exists is None:
            return base
        suffix = "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
        base = (base[:91] + "-" + suffix)[:100]
    raise AppError("guest_name_conflict", "访客昵称冲突，请换一个备注", 409)


async def _new_session(
    db: AsyncSession,
    grant: TempGrant,
    settings,
    label: str | None,
) -> tuple[Group, Member]:
    owner_human = (
        await db.execute(
            select(Member).where(
                Member.owner_id == grant.owner_id, Member.human.is_(True)
            )
        )
    ).scalars().first()
    guest_name = await _guest_name(db, label)
    guest = Member(name=guest_name, platform="guest", human=True, owner_id=None)
    db.add(guest)
    await db.flush()
    group = Group(
        name=f"临时会话 · {guest_name}"[:100],
        kind="temp",
        visibility="private",
        rules=settings.temp_guest_policy,
        invite_code=gen_invite_code(),
        owner_member_id=owner_human.id if owner_human is not None else grant.agent_member_id,
    )
    db.add(group)
    await db.flush()
    db.add(GroupMember(group_id=group.id, member_id=grant.agent_member_id, role="member"))
    db.add(GroupMember(group_id=group.id, member_id=guest.id, role="member"))
    now = _now()
    access_minutes = _minutes_left(grant.expires_at, now)
    max_minutes = _minutes_left(grant.max_access_at, now)
    # 不写死 UTC 墙钟：消息是存下来的文本，客户端按浏览器时区渲染更可靠
    await add_system_message(
        db,
        group,
        guest,
        "临时访客已进入。本次会话由发起人开启，"
        f"最多 {grant.max_messages} 条；"
        f"本次对话剩余约 {access_minutes} 分钟"
        "（页面按你本机的时区显示截止时刻），"
        f"发起人最晚可在约 {max_minutes} 分钟内为你续期。",
    )
    grant.group_id = group.id
    grant.guest_member_id = guest.id
    return group, guest


async def current_temp_guest(request: Request, db: AsyncSession):
    """访客鉴权：Bearer 只查 temp_grants.session_token_hash，绝不查 members.token_hash。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise unauthorized("temp session required")
    token = auth[len("Bearer "):].strip()
    if not token:
        raise unauthorized("temp session required")
    grant = (
        await db.execute(
            select(TempGrant).where(
                TempGrant.session_token_hash == sha256_hex(token)
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise unauthorized("temp session required")

    now = _now()
    if now >= as_utc(grant.max_access_at):
        await expire_temp_grant(db, grant, dissolve=False)
        await db.commit()
        raise AppError("max_access_reached", "已超过最大允许访问时间", 410)
    if now >= as_utc(grant.expires_at):
        # 清 token，但先不 dissolve：给已开始的 agent 回复留宽限期
        await expire_temp_grant(db, grant, dissolve=False)
        await db.commit()
        raise AppError("temp_expired", "临时会话已到期", 410)
    if grant.revoked_at is not None:
        raise AppError("grant_revoked", "临时授权已被撤销", 410)
    if grant.closed_at is not None:
        raise AppError("temp_closed", "临时会话已结束", 410)
    if grant.redeemed_at is None:
        raise unauthorized("temp session required")

    group = await db.get(Group, grant.group_id) if grant.group_id else None
    guest = await db.get(Member, grant.guest_member_id) if grant.guest_member_id else None
    if group is None or guest is None or group.dissolved_at is not None:
        raise AppError("temp_closed", "临时会话已结束", 410)
    grant.last_seen_at = now
    await db.commit()
    return grant, group, guest


# ---------------------------------------------------------------- owner


@router.post("/web/agents/{agent_id}/temp-grants")
async def create_temp_grant(
    agent_id: str,
    body: TempGrantCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    _owner, _human = await owner_and_human(db, owner_id)
    settings = request.app.state.settings
    if not settings.temp_guest_policy.strip():
        raise AppError("temp_policy_missing", "全局访客策略未配置，临时授权已关闭", 503)
    if body.maxAccessMinutes < body.accessMinutes:
        raise bad_request("最大允许访问时间不能小于访问时长", "temp_invalid_access")
    if body.maxAccessMinutes > settings.temp_grant_max_max_minutes:
        raise bad_request(
            f"最大允许访问时间不能超过 {settings.temp_grant_max_max_minutes} 分钟",
            "temp_limit",
        )
    if body.accessMinutes > settings.temp_grant_max_minutes:
        raise bad_request(
            f"访问时长不能超过 {settings.temp_grant_max_minutes} 分钟", "temp_limit"
        )
    if body.maxMessages > settings.temp_grant_max_messages:
        raise bad_request(
            f"消息条数不能超过 {settings.temp_grant_max_messages}", "temp_limit"
        )
    agent = await get_member_or_404(db, agent_id)
    if agent.human or agent.owner_id != owner_id or agent.token_revoked_at is not None:
        raise forbidden("只能为自己的在线 agent 创建临时授权")

    now = _now()
    active = (
        await db.execute(
            select(func.count())
            .select_from(TempGrant)
            .where(
                TempGrant.owner_id == owner_id,
                TempGrant.agent_member_id == agent.id,
                TempGrant.revoked_at.is_(None),
                TempGrant.closed_at.is_(None),
                TempGrant.expires_at > now,
            )
        )
    ).scalar() or 0
    if active >= settings.temp_max_active_grants_per_agent:
        raise AppError("temp_limit", "该 agent 的活跃临时授权过多，请先结束旧的", 409)

    raw_code = gen_temp_code()
    grant = TempGrant(
        owner_id=owner_id,
        agent_member_id=agent.id,
        label=" ".join(body.label.split())[:20] if body.label else None,
        code_hash=sha256_hex(normalize_temp_code(raw_code)),
        access_seconds=body.accessMinutes * 60,
        max_access_seconds=body.maxAccessMinutes * 60,
        max_access_at=now + dt.timedelta(minutes=body.maxAccessMinutes),
        max_messages=body.maxMessages,
        expires_at=now + dt.timedelta(minutes=body.accessMinutes),
    )
    db.add(grant)
    await db.commit()
    await db.refresh(grant)

    data = _grant_dict(grant, agent, now)
    data["code"] = raw_code
    data["shareText"] = _share_text(request, grant, raw_code)
    return {"schema": 1, "grant": data}


@router.get("/web/temp-grants")
async def list_temp_grants(
    request: Request,
    agentId: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    now = _now()
    q = (
        select(TempGrant)
        .where(TempGrant.owner_id == owner_id)
        .order_by(TempGrant.created_at.desc(), TempGrant.id.desc())
        .limit(100)
    )
    if agentId:
        agent = await get_member_or_404(db, agentId)
        if agent.owner_id != owner_id:
            raise forbidden("只能查看自己的临时授权")
        q = q.where(TempGrant.agent_member_id == agent.id)
    grants = list((await db.execute(q)).scalars().all())
    agents = {}
    if grants:
        rows = await db.execute(
            select(Member).where(
                Member.id.in_({g.agent_member_id for g in grants})
            )
        )
        agents = {m.id: m for m in rows.scalars().all()}
    return {
        "schema": 1,
        "grants": [_grant_dict(g, agents.get(g.agent_member_id), now) for g in grants],
    }


@router.get("/web/temp-grants/{grant_id}")
async def get_temp_grant(
    grant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    grant = await _owner_grant(db, owner_id, grant_id)
    agent = await db.get(Member, grant.agent_member_id)
    return {"schema": 1, "grant": _grant_dict(grant, agent)}


@router.post("/web/temp-grants/{grant_id}/renew")
async def renew_temp_grant(
    grant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """同一会话续验证码：保留群/访客/消息，重新给一个活动窗口和一次性码。"""
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    settings = request.app.state.settings
    if not settings.temp_guest_policy.strip():
        raise AppError("temp_policy_missing", "全局访客策略未配置，临时授权已关闭", 503)
    grant = await _owner_grant(db, owner_id, grant_id)
    now = _now()
    if grant.revoked_at is not None:
        raise AppError("temp_closed", "临时授权已被撤销，不能续期", 410)
    if now >= as_utc(grant.max_access_at):
        raise AppError("max_access_reached", "已超过最大允许访问时间，不能续期", 410)
    if grant.max_access_seconds < grant.access_seconds:
        raise AppError(
            "temp_invalid",
            "最大允许访问时间小于访问时长，请重新创建临时授权",
            409,
        )

    group = await db.get(Group, grant.group_id) if grant.group_id else None
    if group is not None:
        group.dissolved_at = None
        group.rules = settings.temp_guest_policy
        await add_system_message(db, group, human, "发起人续期了临时会话，历史消息保留。")

    raw_code = gen_temp_code()
    grant.code_hash = sha256_hex(normalize_temp_code(raw_code))
    grant.session_token_hash = None
    grant.redeemed_at = None
    grant.closed_at = None
    grant.close_reason = None
    grant.messages_used = 0
    grant.expires_at = now + dt.timedelta(seconds=grant.access_seconds)
    grant.max_access_at = now + dt.timedelta(seconds=grant.max_access_seconds)
    await db.commit()
    await db.refresh(grant)

    agent = await db.get(Member, grant.agent_member_id)
    data = _grant_dict(grant, agent, now)
    data["code"] = raw_code
    data["shareText"] = _share_text(request, grant, raw_code)
    return {"schema": 1, "grant": data}


@router.delete("/web/temp-grants/{grant_id}")
async def revoke_temp_grant(
    grant_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    grant = await _owner_grant(db, owner_id, grant_id)
    if grant.revoked_at is not None or grant.closed_at is not None:
        return {"schema": 1, "ok": True, "status": temp_grant_status(grant)}
    group = await db.get(Group, grant.group_id) if grant.group_id else None
    if group is not None and group.dissolved_at is None:
        await add_system_message(db, group, human, "发起人结束了临时会话")
    await close_temp_grant(db, grant, "revoked")
    await db.commit()
    return {"schema": 1, "ok": True, "status": "revoked"}


@router.get("/web/temp-grants/{grant_id}/messages")
async def temp_transcript(
    grant_id: str,
    request: Request,
    before: int | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    grant = await _owner_grant(db, owner_id, grant_id)
    if grant.group_id is None:
        return {"schema": 1, "messages": []}
    page = max(1, min(limit, 200))
    q = select(Message).where(Message.group_id == grant.group_id)
    if before is not None:
        q = q.where(Message.seq < before)
    msgs = list(
        (
            await db.execute(q.order_by(Message.seq.desc()).limit(page))
        ).scalars().all()
    )
    msgs.reverse()
    out = []
    for m in msgs:
        author = await db.get(Member, m.author_id)
        out.append(message_dict(m, author, human))
    return {"schema": 1, "messages": out}


# ---------------------------------------------------------------- guest


@router.post("/temp/redeem")
async def temp_redeem(
    body: TempRedeem,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    settings = request.app.state.settings
    cache = request.app.state.cache
    if not settings.temp_guest_policy.strip():
        raise AppError("temp_policy_missing", "全局访客策略未配置，临时授权已关闭", 503)
    ip = request.client.host if request.client else "unknown"
    attempt_key = f"martin:temp:attempt:{ip}"
    attempts = int(await cache.get(attempt_key) or 0)
    if attempts >= settings.temp_code_attempt_limit:
        raise AppError("too_many_attempts", "尝试次数过多，请稍后再试", 429)

    grant = (
        await db.execute(
            select(TempGrant).where(TempGrant.public_id == body.sessionId)
        )
    ).scalar_one_or_none()
    expected_hash = sha256_hex(normalize_temp_code(body.code))
    if grant is None or grant.code_hash != expected_hash:
        await cache.incr(attempt_key, settings.temp_code_attempt_window_seconds)
        raise AppError("invalid_code", "验证码无效或已失效", 404)

    now = _now()
    if now >= as_utc(grant.max_access_at):
        raise AppError("max_access_reached", "已超过最大允许访问时间", 410)
    if grant.revoked_at is not None:
        raise AppError("grant_revoked", "临时授权已被撤销", 410)
    if grant.closed_at is not None:
        raise AppError("temp_closed", "临时会话已结束", 410)
    if grant.redeemed_at is not None:
        raise AppError("already_redeemed", "验证码已被使用，请向邀请者索取新的验证码", 409)
    if now >= as_utc(grant.expires_at):
        raise AppError("code_expired", "验证码已过期", 410)

    claimed = (
        await db.execute(
            update(TempGrant)
            .where(
                TempGrant.id == grant.id,
                TempGrant.code_hash == expected_hash,
                TempGrant.redeemed_at.is_(None),
                TempGrant.revoked_at.is_(None),
                TempGrant.closed_at.is_(None),
                TempGrant.expires_at > now,
                TempGrant.max_access_at > now,
            )
            .values(redeemed_at=now, redeemed_ip=ip)
            .returning(TempGrant.id)
        )
    ).scalar_one_or_none()
    if claimed is None:
        await db.rollback()
        current = (
            await db.execute(select(TempGrant).where(TempGrant.id == grant.id))
        ).scalar_one()
        if as_utc(current.max_access_at) <= now:
            raise AppError("max_access_reached", "已超过最大允许访问时间", 410)
        if current.revoked_at is not None:
            raise AppError("grant_revoked", "临时授权已被撤销", 410)
        if current.closed_at is not None:
            raise AppError("temp_closed", "临时会话已结束", 410)
        if current.redeemed_at is not None:
            raise AppError(
                "already_redeemed", "验证码已被使用，请向邀请者索取新的验证码", 409
            )
        raise AppError("code_expired", "验证码已过期", 410)
    grant.redeemed_at = now
    grant.redeemed_ip = ip
    await cache.delete(attempt_key)

    agent = await db.get(Member, grant.agent_member_id)
    if agent is None or agent.token_revoked_at is not None:
        await db.rollback()
        raise AppError("agent_unavailable", "目标 agent 已不可用，请联系邀请者", 410)

    group = await db.get(Group, grant.group_id) if grant.group_id else None
    guest = await db.get(Member, grant.guest_member_id) if grant.guest_member_id else None

    # 核销后再给满一个完整访问窗口：码本身的时长是「核销有效期」，
    # 访客不该因为发起人发链接慢了几十秒就少用一分钟；上限仍是 max_access_at。
    window_end = min(
        now + dt.timedelta(seconds=grant.access_seconds), grant.max_access_at
    )
    if window_end > grant.expires_at:
        grant.expires_at = window_end

    if group is not None and guest is not None:
        # 续期后重新进入：复用同一个群/访客，历史消息原样保留
        group.dissolved_at = None
        group.rules = settings.temp_guest_policy
        await add_system_message(db, group, guest, "临时会话已续期，历史消息保留。")
    else:
        group, guest = await _new_session(
            db, grant, settings, body.label or grant.label
        )

    token = gen_token()
    grant.session_token_hash = sha256_hex(token)
    grant.group_id = group.id
    grant.guest_member_id = guest.id
    grant.last_seen_at = now
    await db.commit()
    await db.refresh(group)
    await db.refresh(grant)

    states = await push_states(db, [agent], request.app.state.wake_bus)
    reach = states.get(agent.id, {}).get("reach", "away")
    return {
        "schema": 1,
        "sessionToken": token,
        "temp": _temp_state(grant, agent, group, reach, now),
        "messages": [],
    }


@router.get("/temp/session")
async def temp_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    grant, group, _guest = await current_temp_guest(request, db)
    agent = await db.get(Member, grant.agent_member_id)
    states = await push_states(db, [agent], request.app.state.wake_bus)
    reach = states.get(agent.id, {}).get("reach", "away")
    return {"schema": 1, "temp": _temp_state(grant, agent, group, reach)}


@router.get("/temp/messages")
async def temp_messages(
    request: Request,
    after: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    grant, group, guest = await current_temp_guest(request, db)
    page = max(1, min(limit, 200))
    msgs = list(
        (
            await db.execute(
                select(Message)
                .where(Message.group_id == group.id, Message.seq > after)
                .order_by(Message.seq.asc())
                .limit(page)
            )
        ).scalars().all()
    )
    if msgs:
        gm = (
            await db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group.id,
                    GroupMember.member_id == guest.id,
                )
            )
        ).scalar_one_or_none()
        if gm is not None and msgs[-1].seq > gm.last_read_seq:
            gm.last_read_seq = msgs[-1].seq
        await db.commit()
    out = []
    for m in msgs:
        author = await db.get(Member, m.author_id)
        out.append(message_dict(m, author, guest))
    return {
        "schema": 1,
        "messages": out,
        "cursor": msgs[-1].seq if msgs else after,
    }


@router.post("/temp/messages")
async def temp_send(
    body: TempMessageSend,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    settings = request.app.state.settings
    cache = request.app.state.cache
    grant, group, guest = await current_temp_guest(request, db)
    status = temp_grant_status(grant)
    if status == "exhausted":
        raise AppError("quota_exhausted", "本次临时会话的消息条数已用尽", 429)
    if status != "active":
        raise AppError("temp_closed", "临时会话已结束", 410)
    if len(body.text) > settings.message_max_length:
        raise AppError("text_too_long", "消息超过长度上限", 422)

    if body.opKey:
        existing = (
            await db.execute(
                select(Message).where(
                    Message.author_id == guest.id, Message.op_key == body.opKey
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {
                "schema": 1,
                "seq": existing.seq,
                "remainingMessages": max(0, grant.max_messages - grant.messages_used),
                "status": temp_grant_status(grant),
            }

    if settings.temp_guest_min_interval_seconds > 0:
        key = f"martin:temp:last:{grant.id}"
        last = await cache.get(key)
        now_ts = _now().timestamp()
        if last is not None and now_ts - float(last) < settings.temp_guest_min_interval_seconds:
            raise AppError("rate_limited", "发送太快，请稍后再试", 429)
        await cache.set(key, str(now_ts), settings.temp_guest_min_interval_seconds)

    now = _now()
    used = (
        await db.execute(
            update(TempGrant)
            .where(
                TempGrant.id == grant.id,
                TempGrant.revoked_at.is_(None),
                TempGrant.closed_at.is_(None),
                TempGrant.expires_at > now,
                TempGrant.max_access_at > now,
                TempGrant.messages_used < TempGrant.max_messages,
            )
            .values(messages_used=TempGrant.messages_used + 1)
            .returning(TempGrant.messages_used)
        )
    ).scalar_one_or_none()
    if used is None:
        await db.rollback()
        current = (
            await db.execute(select(TempGrant).where(TempGrant.id == grant.id))
        ).scalar_one()
        current_status = temp_grant_status(current)
        if current_status == "exhausted":
            raise AppError("quota_exhausted", "本次临时会话的消息条数已用尽", 429)
        if as_utc(current.max_access_at) <= now:
            raise AppError("max_access_reached", "已超过最大允许访问时间", 410)
        if current_status == "expired":
            raise AppError("temp_expired", "临时会话已到期", 410)
        raise AppError("temp_closed", "临时会话已结束", 410)
    grant.messages_used = used

    payload = MessageSend(
        text=body.text,
        mentions=[],
        priority="normal",
        ignoreNew=True,
        opKey=body.opKey,
    )
    result = await send_message(
        db,
        settings,
        cache,
        group,
        guest,
        payload,
        bus=request.app.state.wake_bus,
        kick=request.app.state.dispatcher.kick,
        allow_text_mentions=False,
    )
    await db.refresh(grant)
    return {
        "schema": 1,
        "seq": result["seq"],
        "remainingMessages": max(0, grant.max_messages - grant.messages_used),
        "status": temp_grant_status(grant),
    }


@router.post("/temp/close")
async def temp_close(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    grant, group, guest = await current_temp_guest(request, db)
    if group.dissolved_at is None:
        await add_system_message(db, group, guest, "访客结束了临时会话")
    await close_temp_grant(db, grant, "guest_closed")
    await db.commit()
    return {"schema": 1, "closed": True}
