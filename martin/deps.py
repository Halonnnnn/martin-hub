import datetime as dt

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .errors import AppError, unauthorized
from .models import Admin, Member, Owner, ReceiverLease
from .security import sha256_hex


def make_session_signer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="martin-web-session")


def make_admin_signer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="martin-admin-session")


async def lookup_token_member(
    db: AsyncSession, settings: Settings, cache, token: str
) -> Member | None:
    token_hash = sha256_hex(token)
    cache_key = f"martin:token:{token_hash}"
    member_id_raw = await cache.get(cache_key)
    member: Member | None = None
    if member_id_raw is not None:
        member = await db.get(Member, int(member_id_raw))
        if member is None or member.token_hash != token_hash or member.token_revoked_at is not None:
            return None
    else:
        row = await db.execute(
            select(Member).where(
                Member.token_hash == token_hash, Member.token_revoked_at.is_(None)
            )
        )
        member = row.scalar_one_or_none()
        if member is not None:
            await cache.set(cache_key, str(member.id), settings.token_cache_ttl)
    if member is not None and member.owner_id is not None:
        owner = await db.get(Owner, member.owner_id)
        if owner is None or owner.disabled_at is not None:
            return None
    return member


async def current_member(request: Request, db: AsyncSession):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise unauthorized()
    token = auth[len("Bearer "):].strip()
    if not token:
        raise unauthorized()
    member = await lookup_token_member(
        db, request.app.state.settings, request.app.state.cache, token
    )
    if member is None:
        raise unauthorized()
    member.last_seen_at = dt.datetime.now(dt.timezone.utc)
    await db.commit()
    return member


async def check_fence(request: Request, db: AsyncSession, member: Member) -> None:
    """仅当请求自带 fence 头时校验租约，保证不带头的老客户端零影响。"""
    fence = request.headers.get("X-Martin-Fence")
    if not fence:
        return
    lease = await db.get(ReceiverLease, member.id)
    now = dt.datetime.now(dt.timezone.utc)
    if lease is None or lease.fence != fence or lease.expires_at <= now:
        raise AppError("lease_lost", "listener 租约已失效", 409)


async def current_web_owner_id(request: Request, db: AsyncSession) -> int:
    """Web 会话校验：签名 + 未停用 + 会话版本。

    改密码、管理员重置密码或停用账号都会 bump session_version，
    之前签发的 cookie 立即失效（而不是继续有效到 TTL 结束）。
    """
    raw = request.cookies.get("martin_session")
    if not raw:
        raise unauthorized("web session required")
    signer: URLSafeTimedSerializer = request.app.state.session_signer
    try:
        payload = signer.loads(
            raw, max_age=request.app.state.settings.web_session_ttl
        )
    except (BadSignature, SignatureExpired):
        raise unauthorized("web session invalid or expired")
    owner = await db.get(Owner, int(payload["owner_id"]))
    if owner is None or owner.disabled_at is not None:
        raise unauthorized("web session invalid or expired")
    if int(payload.get("sv", 0)) != int(owner.session_version or 0):
        raise unauthorized("web session invalid or expired")
    return owner.id


async def current_admin(request: Request, db: AsyncSession) -> Admin:
    raw = request.cookies.get("martin_admin_session")
    if not raw:
        raise unauthorized("admin session required")
    signer: URLSafeTimedSerializer = request.app.state.admin_signer
    try:
        payload = signer.loads(
            raw, max_age=request.app.state.settings.admin_session_ttl
        )
    except (BadSignature, SignatureExpired):
        raise unauthorized("admin session invalid or expired")
    admin = await db.get(Admin, int(payload["admin_id"]))
    if admin is None or admin.disabled_at is not None:
        raise unauthorized("admin disabled or missing")
    return admin
