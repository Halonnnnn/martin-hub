import datetime as dt

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..admin import build_pair_instruction, create_owner_with_human
from ..deps import current_web_owner_id, make_session_signer
from ..errors import AppError, bad_request, forbidden, not_found, unauthorized
from ..models import (
    Admin,
    Group,
    GroupMember,
    Member,
    Message,
    Owner,
    PairingCode,
)
from ..schemas import (
    ChatCreate,
    FriendRequestAction,
    FriendRequestCreate,
    GroupCreate,
    JoinByInvite,
    MessageSend,
    WebGroupAgentInvite,
    WebGroupArchive,
    WebLogin,
    WebPairCode,
    WebPasswordChange,
    WebSetup,
    WebThemeUpdate,
)
from ..security import gen_pairing_code, hash_password, sha256_hex, verify_password
from ..serialize import group_dict, member_dict, message_dict
from ..services import (
    active_membership,
    get_group_or_404,
    get_member_or_404,
    push_states,
    reach_for_members,
    send_message,
)
from .friends import (
    list_friend_members,
    open_chat,
    pending_requests,
    remove_friend,
    request_dict,
    respond_request,
    send_friend_request,
)
from .groups import (
    add_system_message,
    create_group_for,
    group_from_invite,
    join_group,
)

router = APIRouter()

COOKIE_NAME = "martin_session"
ADMIN_COOKIE_NAME = "martin_admin_session"


async def get_db(request: Request):
    async with request.app.state.sessionmaker() as session:
        yield session


async def owner_and_human(db: AsyncSession, owner_id: int) -> tuple[Owner, Member]:
    owner = await db.get(Owner, owner_id)
    if owner is None:
        raise unauthorized("web session invalid")
    row = await db.execute(
        select(Member).where(Member.owner_id == owner_id, Member.human.is_(True))
    )
    human = row.scalars().first()
    if human is None:
        raise not_found("owner 的 human 成员不存在，请运行 martin admin init")
    return owner, human


def _set_session_cookie(request: Request, response: Response, owner: Owner) -> None:
    signer = request.app.state.session_signer
    token = signer.dumps(
        {"owner_id": owner.id, "sv": owner.session_version or 0}
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=request.app.state.settings.web_session_ttl,
        path="/",
    )


async def _authenticate_owner(db: AsyncSession, username: str, password: str) -> Owner:
    row = await db.execute(select(Owner).where(Owner.display_name == username))
    owner = row.scalar_one_or_none()
    if owner is None or not verify_password(password, owner.password_hash):
        raise AppError("invalid_credentials", "用户名或密码错误", 401)
    if owner.disabled_at is not None:
        raise AppError("owner_disabled", "账号已停用", 403)
    if owner.approved_at is None:
        raise AppError("pending_approval", "账号正在等待管理员审批，审批通过后才能登录", 403)
    return owner


@router.post("/web/login")
async def web_login(body: WebLogin, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    owner = await _authenticate_owner(db, body.username, body.password)
    _set_session_cookie(request, response, owner)
    return {"schema": 1, "ok": True, "owner": {"id": owner.id, "displayName": owner.display_name}}


@router.post("/web/session")
async def web_session(body: WebLogin, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """统一登录：用户名命中 admins 表即为管理员，否则按普通用户校验。"""
    admin = (
        await db.execute(select(Admin).where(Admin.username == body.username))
    ).scalar_one_or_none()
    if admin is not None:
        if not verify_password(body.password, admin.password_hash):
            raise AppError("invalid_credentials", "用户名或密码错误", 401)
        if admin.disabled_at is not None:
            raise AppError("admin_disabled", "管理员账号已停用", 403)
        token = request.app.state.admin_signer.dumps({"admin_id": admin.id})
        response.set_cookie(
            ADMIN_COOKIE_NAME,
            token,
            httponly=True,
            samesite="lax",
            max_age=request.app.state.settings.admin_session_ttl,
            path="/",
        )
        response.delete_cookie(COOKIE_NAME, path="/")
        return {
            "schema": 1,
            "kind": "admin",
            "admin": {"id": admin.id, "username": admin.username},
        }

    owner = await _authenticate_owner(db, body.username, body.password)
    _set_session_cookie(request, response, owner)
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
    return {
        "schema": 1,
        "kind": "owner",
        "owner": {"id": owner.id, "displayName": owner.display_name},
    }


@router.get("/web/setup-status")
async def web_setup_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owners = (await db.execute(select(func.count(Owner.id)))).scalar() or 0
    return {"schema": 1, "needsSetup": owners == 0}


@router.post("/web/setup")
async def web_setup(
    body: WebSetup,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """首跑建号：只在库里没有任何用户时可用，避免覆盖已有账号体系。"""
    owners = (await db.execute(select(func.count(Owner.id)))).scalar() or 0
    if owners:
        raise AppError("already_initialized", "系统已有用户，请直接登录", 409)
    owner = await create_owner_with_human(db, body.username, body.password)
    owner.approved_at = dt.datetime.now(dt.timezone.utc)
    await db.commit()
    _set_session_cookie(request, response, owner)
    return {
        "schema": 1,
        "ok": True,
        "owner": {"id": owner.id, "displayName": owner.display_name},
    }


@router.post("/web/register")
async def web_register(
    body: WebSetup,
    db: AsyncSession = Depends(get_db),
):
    """开放注册：创建待审批账号，管理员审批前不能登录。"""
    owner = await create_owner_with_human(db, body.username, body.password)
    return {
        "schema": 1,
        "ok": True,
        "pending": True,
        "owner": {"id": owner.id, "displayName": owner.display_name},
        "message": "注册已提交，等待管理员审批后即可登录。",
    }


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@router.post("/web/pairing-codes")
async def web_create_pair_code(
    body: WebPairCode,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    owner, human = await owner_and_human(db, owner_id)
    group: Group | None = None
    if body.groupId is not None:
        group = (
            await db.execute(
                select(Group).where(
                    Group.public_id == body.groupId, Group.dissolved_at.is_(None)
                )
            )
        ).scalar_one_or_none()
        if group is None:
            raise bad_request("群不存在", "invalid_group")
        gm = (
            await db.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group.id,
                    GroupMember.member_id == human.id,
                    GroupMember.removed_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if gm is None:
            raise bad_request("你不在这个群里", "invalid_group")
    code = gen_pairing_code()
    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
        hours=request.app.state.settings.pairing_code_ttl_hours
    )
    row = PairingCode(
        code_hash=sha256_hex(code),
        owner_id=owner.id,
        group_id=group.id if group is not None else None,
        reclaim_name=body.reclaimName,
        expires_at=expires,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {
        "schema": 1,
        "id": row.id,
        "code": code,
        "expiresAt": _iso(row.expires_at),
        "instruction": build_pair_instruction(code, body.groupId, body.reclaimName),
    }


@router.get("/web/pairing-codes")
async def web_list_pair_codes(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    now = dt.datetime.now(dt.timezone.utc)
    rows = (
        await db.execute(
            select(PairingCode, Group.public_id, Group.name)
            .outerjoin(Group, Group.id == PairingCode.group_id)
            .where(PairingCode.owner_id == owner_id)
            .order_by(PairingCode.id.desc())
            .limit(50)
        )
    ).all()
    codes = []
    for r, group_public_id, group_name in rows:
        if r.revoked_at is not None:
            status = "revoked"
        elif r.used_at is not None:
            status = "used"
        elif r.expires_at <= now:
            status = "expired"
        else:
            status = "active"
        codes.append(
            {
                "id": r.id,
                "groupId": group_public_id,
                "groupName": group_name,
                "reclaimName": r.reclaim_name,
                "status": status,
                "createdAt": _iso(r.created_at),
                "expiresAt": _iso(r.expires_at),
                "usedAt": _iso(r.used_at),
                "revokedAt": _iso(r.revoked_at),
            }
        )
    return {"schema": 1, "codes": codes}


@router.delete("/web/pairing-codes/{code_id}")
async def web_revoke_pair_code(
    code_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    row = await db.get(PairingCode, code_id)
    if row is None or row.owner_id != owner_id:
        raise not_found("配对码不存在")
    if row.used_at is None and row.revoked_at is None:
        row.revoked_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()
    return {"schema": 1, "revoked": True}


@router.post("/web/logout")
async def web_logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    await current_web_owner_id(request, db)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"schema": 1, "ok": True}


@router.get("/web/me")
async def web_me(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    owner, human = await owner_and_human(db, owner_id)
    return {
        "schema": 1,
        "owner": {
            "id": owner.id,
            "displayName": owner.display_name,
            "createdAt": _iso(owner.created_at),
            "uiTheme": owner.ui_theme,
        },
        "humanMember": {"id": human.public_id, "name": human.name},
    }


@router.patch("/web/me")
async def web_update_me(
    body: WebThemeUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    owner, human = await owner_and_human(db, owner_id)
    owner.ui_theme = body.uiTheme
    await db.commit()
    return {"schema": 1, "owner": {"id": owner.id, "uiTheme": owner.ui_theme}}


@router.get("/web/groups")
async def web_groups(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    rows = await db.execute(
        select(Group, GroupMember.last_read_seq)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(
            GroupMember.member_id == human.id,
            GroupMember.removed_at.is_(None),
            Group.dissolved_at.is_(None),
            Group.kind == "group",
        )
    )
    result = []
    for group, last_read in rows.all():
        unread_row = await db.execute(
            select(func.count(Message.id)).where(
                Message.group_id == group.id,
                Message.seq > last_read,
                Message.recalled_at.is_(None),
                Message.author_id != human.id,
            )
        )
        d = group_dict(group, invite_code=group.invite_code)
        d["unread"] = unread_row.scalar() or 0
        result.append(d)
    return {"schema": 1, "groups": result}


@router.post("/web/groups")
async def web_create_group(
    body: GroupCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await create_group_for(db, human, body)
    return {
        "schema": 1,
        "group": group_dict(group, invite_code=group.invite_code),
    }


@router.post("/web/groups/{group_id}/join")
async def web_join(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """按群码入群：仅限公开群或与群主同一个用户，其余走邀请链接。"""
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await get_group_or_404(db, group_id)
    if group.kind == "direct":
        raise forbidden("私聊会话不可加入")
    owner_member = await db.get(Member, group.owner_member_id)
    same_owner = (
        owner_member is not None
        and human.owner_id is not None
        and human.owner_id == owner_member.owner_id
    )
    if group.visibility != "public" and not same_owner:
        raise forbidden("请通过邀请链接加入该群")
    joined = await join_group(
        db,
        group,
        human,
        limit=request.app.state.settings.members_per_group_limit,
    )
    return {
        "schema": 1,
        "joined": joined,
        "group": group_dict(group, invite_code=group.invite_code),
        "note": "已加入，打个招呼吧" if joined else "已在群中",
    }


@router.post("/web/join")
async def web_join_by_invite(
    body: JoinByInvite,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """登录用户凭邀请链接或群码加入群；入群身份是自己的人类成员。"""
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await group_from_invite(db, body.invite)
    joined = await join_group(
        db,
        group,
        human,
        limit=request.app.state.settings.members_per_group_limit,
    )
    return {
        "schema": 1,
        "joined": joined,
        "group": group_dict(group, invite_code=group.invite_code),
        "note": "已加入，打个招呼吧" if joined else "已在群中",
    }


@router.post("/web/groups/{group_id}/agents")
async def web_add_agent(
    group_id: str,
    body: WebGroupAgentInvite,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """用户把自己的 agent 直接拉进群，不需要再生成配对码或转发邀请链接。"""
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await get_group_or_404(db, group_id)
    if group.kind == "direct":
        raise bad_request("私聊会话不能添加 agent", "invalid_group")
    await active_membership(db, group.id, human.id)
    target = await get_member_or_404(db, body.agentId)
    if target.human or target.owner_id != human.owner_id:
        raise forbidden("只能邀请自己接入的 agent")
    joined = await join_group(
        db,
        group,
        target,
        limit=request.app.state.settings.members_per_group_limit,
    )
    if joined:
        await add_system_message(db, group, human, f"{human.name} 邀请 {target.name} 加入群")
        await db.commit()
    states = await push_states(db, [target], request.app.state.wake_bus)
    state = states.get(target.id, {"reach": "away", "connected": False, "reason": "no_channel"})
    d = member_dict(
        target,
        human,
        reach=state["reach"],
        push_connected=state["connected"],
    )
    d["pushReason"] = state.get("reason")
    return {"schema": 1, "joined": joined, "agent": d}


@router.get("/web/agents")
async def web_agents(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """自己的 agent 列表：接入 Agent 页与「邀请我的 agent 进群」共用。"""
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    rows = await db.execute(
        select(Member)
        .where(Member.owner_id == owner_id, Member.human.is_(False))
        .order_by(Member.created_at.asc(), Member.id.asc())
    )
    agents = list(rows.scalars().all())
    states = await push_states(db, agents, request.app.state.wake_bus)
    groups_by_agent: dict[int, list[dict]] = {}
    if agents:
        group_rows = await db.execute(
            select(GroupMember.member_id, Group)
            .join(Group, Group.id == GroupMember.group_id)
            .where(
                GroupMember.member_id.in_([a.id for a in agents]),
                GroupMember.removed_at.is_(None),
                Group.kind == "group",
                Group.dissolved_at.is_(None),
            )
            .order_by(Group.created_at.asc())
        )
        for member_id, group in group_rows.all():
            groups_by_agent.setdefault(member_id, []).append(
                {"id": group.public_id, "name": group.name}
            )
    out = []
    for agent in agents:
        state = states.get(agent.id, {"reach": "away", "connected": False, "reason": "no_channel"})
        d = member_dict(
            agent,
            human,
            reach=state["reach"],
            push_connected=state["connected"],
        )
        d["pushReason"] = state.get("reason")
        d["groups"] = groups_by_agent.get(agent.id, [])
        d["tokenRevoked"] = agent.token_revoked_at is not None
        out.append(d)
    return {"schema": 1, "agents": out}


@router.post("/web/password")
async def web_change_password(
    body: WebPasswordChange,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """改密码后 bump 会话版本：所有已登录的浏览器都必须重新登录。"""
    owner_id = await current_web_owner_id(request, db)
    owner = await db.get(Owner, owner_id)
    if owner is None or not verify_password(body.currentPassword, owner.password_hash):
        raise AppError("invalid_password", "当前密码不正确", 400)
    if body.newPassword == body.currentPassword:
        raise bad_request("新密码不能与当前密码相同", "same_password")
    owner.password_hash = hash_password(body.newPassword)
    owner.session_version = (owner.session_version or 0) + 1
    await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"schema": 1, "ok": True, "relogin": True}


@router.get("/web/groups/{group_id}/members")
async def web_members(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await get_group_or_404(db, group_id)
    mine = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == human.id,
            GroupMember.removed_at.is_(None),
        )
    )
    if mine.scalar_one_or_none() is None:
        raise forbidden("not a member of this group")
    rows = await db.execute(
        select(Member, GroupMember.role)
        .join(GroupMember, GroupMember.member_id == Member.id)
        .where(
            GroupMember.group_id == group.id,
            GroupMember.removed_at.is_(None),
        )
    )
    pairs = rows.all()
    states = await push_states(db, [m for m, _role in pairs], request.app.state.wake_bus)
    members = []
    for m, role in pairs:
        state = states.get(m.id, {"reach": "away", "connected": False})
        d = member_dict(m, human, reach=state["reach"], push_connected=state["connected"])
        d["role"] = role
        d["pushReason"] = state.get("reason")
        members.append(d)
    return {"schema": 1, "members": members}


@router.post("/web/groups/{group_id}/leave")
async def web_leave_group(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await get_group_or_404(db, group_id)
    if group.kind == "direct":
        raise bad_request("私聊会话请使用结束会话", "direct_no_leave")
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == human.id,
            GroupMember.removed_at.is_(None),
        )
    )
    gm = row.scalar_one_or_none()
    if gm is None:
        raise not_found("你不在这个群里")
    if gm.role == "owner":
        raise bad_request("群主不能退出群聊", "owner_cannot_leave")
    gm.removed_at = dt.datetime.now(dt.timezone.utc)
    await add_system_message(db, group, human, f"{human.name} 离开了群")
    await db.commit()
    return {"schema": 1, "left": True}


@router.delete("/web/groups/{group_id}/members/{member_id}")
async def web_remove_member(
    group_id: str,
    member_id: str,
    request: Request,
    with_agents: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """群主可移出任何成员；用户可移出自己的 agent。移出用户时可连带移出其 agent。"""
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await get_group_or_404(db, group_id)
    if group.kind == "direct":
        raise bad_request("私聊会话不能移出成员", "direct_no_kick")
    mine_row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == human.id,
            GroupMember.removed_at.is_(None),
        )
    )
    mine = mine_row.scalar_one_or_none()
    if mine is None:
        raise forbidden("你不在这个群里")
    target = (
        await db.execute(select(Member).where(Member.public_id == member_id))
    ).scalar_one_or_none()
    if target is None:
        raise not_found("成员不存在")
    if target.id == human.id:
        raise bad_request("退出群聊请使用退出按钮", "use_leave")
    target_row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == target.id,
            GroupMember.removed_at.is_(None),
        )
    )
    tgm = target_row.scalar_one_or_none()
    if tgm is None:
        raise not_found("成员不在群里")
    if target.id == group.owner_member_id:
        raise bad_request("不能移除群主", "cannot_remove_owner")
    own_agent = (
        not target.human
        and target.owner_id is not None
        and target.owner_id == owner_id
    )
    if mine.role != "owner" and not own_agent:
        raise forbidden("仅群主可移出该成员")

    now = dt.datetime.now(dt.timezone.utc)
    tgm.removed_at = now
    removed_agents = 0
    if with_agents and target.human and target.owner_id is not None:
        rows = await db.execute(
            select(GroupMember)
            .join(Member, Member.id == GroupMember.member_id)
            .where(
                GroupMember.group_id == group.id,
                GroupMember.removed_at.is_(None),
                Member.owner_id == target.owner_id,
                Member.human.is_(False),
            )
        )
        for gm in rows.scalars().all():
            gm.removed_at = now
            removed_agents += 1
    text = (
        f"{target.name} 及其 {removed_agents} 个 agent 被移出群"
        if removed_agents
        else f"{target.name} 被移出群"
    )
    await add_system_message(db, group, human, text)
    await db.commit()
    return {"schema": 1, "removed": True, "removedAgents": removed_agents}


@router.post("/web/groups/{group_id}/archive")
async def web_archive_group(
    group_id: str,
    body: WebGroupArchive,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """群主归档 / 取消归档：归档后只能查看历史消息。"""
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await get_group_or_404(db, group_id)
    if group.kind == "direct":
        raise bad_request("私聊会话不能归档", "direct_no_archive")
    mine_row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == human.id,
            GroupMember.removed_at.is_(None),
        )
    )
    mine = mine_row.scalar_one_or_none()
    if mine is None or mine.role != "owner":
        raise forbidden("仅群主可归档群聊")
    if body.archived:
        group.archived_at = dt.datetime.now(dt.timezone.utc)
        await add_system_message(db, group, human, f"{human.name} 归档了群，成员只能查看历史消息")
    else:
        group.archived_at = None
        await add_system_message(db, group, human, f"{human.name} 取消了归档，群聊恢复")
    await db.commit()
    return {"schema": 1, "archived": group.archived_at is not None}


@router.delete("/web/groups/{group_id}")
async def web_dissolve_group(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await get_group_or_404(db, group_id)
    if group.kind == "direct":
        raise bad_request("私聊会话请使用结束会话", "direct_no_dissolve")
    mine_row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == human.id,
            GroupMember.removed_at.is_(None),
        )
    )
    mine = mine_row.scalar_one_or_none()
    if mine is None or mine.role != "owner":
        raise forbidden("仅群主可解散群聊")
    group.dissolved_at = dt.datetime.now(dt.timezone.utc)
    await add_system_message(db, group, human, f"{human.name} 解散了群 {group.name}")
    await db.commit()
    return {"schema": 1, "dissolved": True}


@router.get("/web/groups/{group_id}/messages")
async def web_messages(
    group_id: str,
    request: Request,
    before: int | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await get_group_or_404(db, group_id)
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == human.id,
            GroupMember.removed_at.is_(None),
        )
    )
    gm = row.scalar_one_or_none()
    if gm is None:
        raise forbidden("not a member of this group")
    q = select(Message).where(Message.group_id == group.id)
    if before is not None:
        q = q.where(Message.seq < before)
    q = q.order_by(Message.seq.desc()).limit(min(limit, 200))
    msgs = (await db.execute(q)).scalars().all()
    msgs = list(reversed(msgs))
    out = []
    for m in msgs:
        a = await db.get(Member, m.author_id)
        out.append(message_dict(m, a, human, new=False))
    # 打开会话读取最新消息即视为已读，历史翻页（before）不推进水位
    if before is None and msgs and msgs[-1].seq > gm.last_read_seq:
        gm.last_read_seq = msgs[-1].seq
        await db.commit()
    return {"schema": 1, "messages": out}


@router.post("/web/groups/{group_id}/messages")
async def web_send(
    group_id: str,
    body: MessageSend,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await get_group_or_404(db, group_id)
    row = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.member_id == human.id,
            GroupMember.removed_at.is_(None),
        )
    )
    if row.scalar_one_or_none() is None:
        raise forbidden("not a member of this group")
    if body.opKey is None:
        body.opKey = f"web-{human.id}-{dt.datetime.now(dt.timezone.utc).timestamp()}"
    # 用户在 Web 实时看消息流，409 未读检查只约束 agent
    body.ignoreNew = True
    result = await send_message(
        db,
        request.app.state.settings,
        request.app.state.cache,
        group,
        human,
        body,
        bus=request.app.state.wake_bus,
        kick=request.app.state.dispatcher.kick,
    )
    return {"schema": 1, **result}


@router.get("/web/chats")
async def web_chats(request: Request, db: AsyncSession = Depends(get_db)):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    rows = await db.execute(
        select(Group, GroupMember.last_read_seq)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(
            GroupMember.member_id == human.id,
            GroupMember.removed_at.is_(None),
            Group.dissolved_at.is_(None),
            Group.kind == "direct",
        )
    )
    result = []
    for group, last_read in rows.all():
        peer_row = await db.execute(
            select(Member)
            .join(GroupMember, GroupMember.member_id == Member.id)
            .where(
                GroupMember.group_id == group.id,
                GroupMember.removed_at.is_(None),
                Member.id != human.id,
            )
        )
        peer = peer_row.scalars().first()
        # 历史数据里可能存在跨用户的 agent 私聊；隔离后不再展示（数据保留）
        if peer is None or (
            not peer.human and peer.owner_id != human.owner_id
        ):
            continue
        unread_row = await db.execute(
            select(func.count(Message.id)).where(
                Message.group_id == group.id,
                Message.seq > last_read,
                Message.recalled_at.is_(None),
                Message.author_id != human.id,
            )
        )
        d = group_dict(group)
        d["unread"] = unread_row.scalar() or 0
        d["peer"] = member_dict(peer, human) if peer else None
        result.append(d)
    return {"schema": 1, "chats": result}


@router.post("/web/chats")
async def web_open_chat(
    body: ChatCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    result = await open_chat(db, human, body.with_)
    return {"schema": 1, **result}


@router.delete("/web/chats/{group_id}")
async def web_close_chat(
    group_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """用户结束一对一会话（人类之间没有 token，不能走 DELETE /groups/{id}）。"""
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    group = await get_group_or_404(db, group_id)
    if group.kind != "direct":
        raise bad_request("只有一对一会话可以这样结束", "not_direct")
    await active_membership(db, group.id, human.id)
    group.dissolved_at = dt.datetime.now(dt.timezone.utc)
    await add_system_message(db, group, human, f"{human.name} 结束了会话")
    await db.commit()
    return {"schema": 1, "dissolved": True}


@router.get("/web/friends")
async def web_friends(request: Request, db: AsyncSession = Depends(get_db)):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    friends = await list_friend_members(db, human)
    reaches = await reach_for_members(db, friends, request.app.state.wake_bus)
    return {
        "schema": 1,
        "friends": [member_dict(m, human, reach=reaches.get(m.id, "away")) for m in friends],
    }


@router.post("/web/friends")
async def web_add_friend(
    body: FriendRequestCreate, request: Request, db: AsyncSession = Depends(get_db)
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    fr = await send_friend_request(db, human, body.to, body.note)
    return {"schema": 1, "ok": True, "request": await request_dict(db, fr, human)}


@router.get("/web/friend-requests")
async def web_friend_requests(request: Request, db: AsyncSession = Depends(get_db)):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    reqs = await pending_requests(db, human)
    return {"schema": 1, "requests": [await request_dict(db, fr, human) for fr in reqs]}


@router.post("/web/friend-requests/{request_id}")
async def web_act_friend_request(
    request_id: int,
    body: FriendRequestAction,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    result = await respond_request(db, human, request_id, body.action)
    return {"schema": 1, **result}


@router.delete("/web/friends/{member_id}")
async def web_delete_friend(
    member_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    owner_id = await current_web_owner_id(request, db)
    _owner, human = await owner_and_human(db, owner_id)
    await remove_friend(db, human, member_id)
    return {"schema": 1, "ok": True}
