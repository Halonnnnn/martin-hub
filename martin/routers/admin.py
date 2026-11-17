import datetime as dt

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..admin import create_owner_with_human
from ..deps import current_admin
from ..errors import AppError, not_found
from ..models import Admin, FriendRequest, Friendship, Group, Member, Message, Owner
from ..schemas import AdminLogin, AdminPasswordReset, AdminUserCreate
from ..security import hash_password, verify_password
from ..serialize import owner_admin_dict

router = APIRouter()

COOKIE_NAME = "martin_admin_session"


async def get_db(request: Request):
    async with request.app.state.sessionmaker() as session:
        yield session


async def _owner_or_404(db: AsyncSession, owner_id: int) -> Owner:
    owner = await db.get(Owner, owner_id)
    if owner is None:
        raise not_found("用户不存在")
    return owner


@router.post("/admin/login")
async def admin_login(
    body: AdminLogin,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    row = await db.execute(select(Admin).where(Admin.username == body.username))
    admin = row.scalar_one_or_none()
    if admin is None or not verify_password(body.password, admin.password_hash):
        raise AppError("invalid_credentials", "用户名或密码错误", 401)
    if admin.disabled_at is not None:
        raise AppError("admin_disabled", "管理员已停用", 403)
    signer = request.app.state.admin_signer
    token = signer.dumps({"admin_id": admin.id})
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=request.app.state.settings.admin_session_ttl,
        path="/",
    )
    return {"schema": 1, "ok": True, "admin": {"id": admin.id, "username": admin.username}}


@router.post("/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"schema": 1, "ok": True}


@router.get("/admin/me")
async def admin_me(request: Request, db: AsyncSession = Depends(get_db)):
    admin = await current_admin(request, db)
    return {"schema": 1, "admin": {"id": admin.id, "username": admin.username}}


@router.get("/admin/users")
async def admin_users(request: Request, db: AsyncSession = Depends(get_db)):
    await current_admin(request, db)
    owners = (await db.execute(select(Owner).order_by(Owner.id))).scalars().all()
    counts = dict(
        (
            await db.execute(
                select(Member.owner_id, func.count(Member.id))
                .where(Member.human.is_(False), Member.owner_id.is_not(None))
                .group_by(Member.owner_id)
            )
        ).all()
    )
    humans = (
        await db.execute(select(Member).where(Member.human.is_(True)))
    ).scalars().all()
    human_by_owner = {m.owner_id: m.name for m in humans}
    return {
        "schema": 1,
        "users": [
            owner_admin_dict(o, counts.get(o.id, 0), human_by_owner.get(o.id))
            for o in owners
        ],
    }


@router.post("/admin/users")
async def admin_create_user(
    body: AdminUserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await current_admin(request, db)
    owner = await create_owner_with_human(db, body.username, body.password)
    owner.approved_at = dt.datetime.now(dt.timezone.utc)
    await db.commit()
    return {
        "schema": 1,
        "user": owner_admin_dict(owner, 0, owner.display_name),
    }


@router.post("/admin/users/{owner_id}/password")
async def admin_reset_password(
    owner_id: int,
    body: AdminPasswordReset,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await current_admin(request, db)
    owner = await _owner_or_404(db, owner_id)
    owner.password_hash = hash_password(body.password)
    # 管理员重置密码同样让该用户所有 Web 会话立即失效
    owner.session_version = (owner.session_version or 0) + 1
    await db.commit()
    return {"schema": 1, "ok": True}


@router.post("/admin/users/{owner_id}/approve")
async def admin_approve_user(
    owner_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    await current_admin(request, db)
    owner = await _owner_or_404(db, owner_id)
    if owner.approved_at is None:
        owner.approved_at = dt.datetime.now(dt.timezone.utc)
        await db.commit()
    return {"schema": 1, "ok": True, "approved": True}


@router.post("/admin/users/{owner_id}/disable")
async def admin_disable_user(
    owner_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    await current_admin(request, db)
    owner = await _owner_or_404(db, owner_id)
    now = dt.datetime.now(dt.timezone.utc)
    owner.disabled_at = now
    # 停用后旧会话不能靠“重新启用”复活
    owner.session_version = (owner.session_version or 0) + 1
    members = (
        await db.execute(select(Member).where(Member.owner_id == owner.id))
    ).scalars().all()
    for m in members:
        m.presence = "offline"
        m.presence_at = now
    await db.commit()
    return {"schema": 1, "ok": True, "disabled": True}


@router.post("/admin/users/{owner_id}/enable")
async def admin_enable_user(
    owner_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    await current_admin(request, db)
    owner = await _owner_or_404(db, owner_id)
    owner.disabled_at = None
    await db.commit()
    return {"schema": 1, "ok": True, "disabled": False}


@router.post("/admin/users/{owner_id}/revoke-tokens")
async def admin_revoke_tokens(
    owner_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    await current_admin(request, db)
    owner = await _owner_or_404(db, owner_id)
    rows = (
        await db.execute(
            select(Member).where(
                Member.owner_id == owner.id,
                Member.human.is_(False),
                Member.token_revoked_at.is_(None),
            )
        )
    ).scalars().all()
    now = dt.datetime.now(dt.timezone.utc)
    cache = request.app.state.cache
    for m in rows:
        m.token_revoked_at = now
        m.presence = "offline"
        m.presence_at = now
        if m.token_hash:
            await cache.delete(f"martin:token:{m.token_hash}")
    await db.commit()
    return {"schema": 1, "ok": True, "revoked": len(rows)}


@router.get("/admin/stats")
async def admin_stats(request: Request, db: AsyncSession = Depends(get_db)):
    await current_admin(request, db)

    async def count(model, *where):
        q = select(func.count(model.id))
        for w in where:
            q = q.where(w)
        return (await db.execute(q)).scalar() or 0

    return {
        "schema": 1,
        "stats": {
            "owners": await count(Owner),
            "pendingOwners": await count(
                Owner, Owner.approved_at.is_(None), Owner.disabled_at.is_(None)
            ),
            "admins": await count(Admin),
            "agents": await count(Member, Member.human.is_(False)),
            "humans": await count(Member, Member.human.is_(True)),
            "groups": await count(Group, Group.kind == "group", Group.dissolved_at.is_(None)),
            "directChats": await count(Group, Group.kind == "direct", Group.dissolved_at.is_(None)),
            "messages": await count(Message),
            "friendships": await count(Friendship),
            "pendingFriendRequests": await count(FriendRequest, FriendRequest.status == "pending"),
        },
    }