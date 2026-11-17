import asyncio
import datetime as dt
import json
import re
import secrets
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, case, delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import current_member
from ..dispatch import wake_payload
from ..errors import AppError, bad_request
from ..models import Group, ReceiverLease, WakeConfig, WakeEvent, WakeQueue
from ..schemas import LeaseAcquire, LeaseRelease, WakeConfigPut, WakeMutePatch
from ..security import encrypt_secret

LEASE_TTL_SECONDS = 90
HOLDER_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")

router = APIRouter()


async def get_db(request: Request):
    async with request.app.state.sessionmaker() as session:
        yield session


def _iso(v: dt.datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


def _config_dict(cfg: WakeConfig | None, connected: bool) -> dict | None:
    if cfg is None:
        return None
    return {
        "kind": cfg.kind,
        "url": cfg.url,
        "enabled": cfg.enabled,
        "muted": cfg.muted,
        "allowFrom": getattr(cfg, "allow_from", "any_member"),
        "allowList": getattr(cfg, "allow_list", []) or [],
        "connected": connected,
        "configuredAt": _iso(cfg.configured_at),
    }


async def _test_webhook(settings, cfg: WakeConfig) -> tuple[bool, str]:
    from ..security import decrypt_secret

    secret = decrypt_secret(settings.secret_key, cfg.secret_cipher) if cfg.secret_cipher else None
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    payload = {"schema": 1, "kind": "wake_test", "at": _iso(dt.datetime.now(dt.timezone.utc))}
    try:
        async with httpx.AsyncClient(
            timeout=settings.webhook_timeout_seconds, trust_env=False
        ) as client:
            resp = await client.post(cfg.url, json=payload, headers=headers)
        if 200 <= resp.status_code < 300:
            return True, f"http {resp.status_code}"
        return False, f"http {resp.status_code}"
    except Exception as exc:
        return False, type(exc).__name__


@router.get("/wake")
async def get_wake(request: Request, db: AsyncSession = Depends(get_db)):
    member = await current_member(request, db)
    cfg = await db.get(WakeConfig, member.id)
    bus = request.app.state.wake_bus
    connected = bool(cfg is not None and cfg.kind == "listener" and bus.is_connected(member.id))
    last = (
        await db.execute(
            select(WakeEvent)
            .where(WakeEvent.member_id == member.id)
            .order_by(WakeEvent.id.desc())
            .limit(1)
        )
    ).scalars().first()
    return {
        "schema": 1,
        "wake": _config_dict(cfg, connected),
        "lastResult": (
            {"result": last.result, "detail": last.detail, "at": _iso(last.created_at)}
            if last
            else None
        ),
    }


@router.put("/wake")
async def put_wake(
    body: WakeConfigPut, request: Request, db: AsyncSession = Depends(get_db)
):
    member = await current_member(request, db)
    settings = request.app.state.settings
    if body.kind == "webhook":
        if not body.url or not body.secret:
            raise bad_request("webhook 需要 url 与 secret", "invalid_wake_config")
        parsed = urlparse(body.url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise bad_request("url 必须是 http/https 地址", "invalid_wake_url")

    cfg = await db.get(WakeConfig, member.id)
    if cfg is None:
        cfg = WakeConfig(member_id=member.id)
        db.add(cfg)
    cfg.kind = body.kind
    cfg.url = body.url if body.kind == "webhook" else None
    cfg.secret_cipher = (
        encrypt_secret(settings.secret_key, body.secret)
        if body.kind == "webhook" and body.secret
        else None
    )
    cfg.enabled = True
    if body.muted is not None:
        cfg.muted = body.muted
    if body.allowFrom is not None:
        cfg.allow_from = body.allowFrom
        if body.allowFrom != "list":
            cfg.allow_list = []
    if body.allowList is not None:
        names = sorted({n.strip() for n in body.allowList if n and n.strip()})
        if any(len(n) > 100 for n in names):
            raise bad_request("allowList 成员名过长", "invalid_wake_config")
        cfg.allow_list = names[:50]
        if cfg.allow_from == "any_member":
            cfg.allow_from = "list"
    cfg.configured_at = dt.datetime.now(dt.timezone.utc)
    await db.commit()

    test = {"ok": True, "detail": "listener 通道无需连通性测试"}
    if body.kind == "webhook":
        ok, detail = await _test_webhook(settings, cfg)
        test = {"ok": ok, "detail": detail}
        if not ok:
            cfg.enabled = False
            await db.commit()
    bus = request.app.state.wake_bus
    return {
        "schema": 1,
        "wake": _config_dict(cfg, bus.is_connected(member.id)),
        "test": test,
    }


@router.patch("/wake")
async def patch_wake(
    body: WakeMutePatch, request: Request, db: AsyncSession = Depends(get_db)
):
    """只切免打扰，不需要重新提交 webhook secret。"""
    member = await current_member(request, db)
    cfg = await db.get(WakeConfig, member.id)
    if cfg is None:
        raise bad_request("尚未配置唤醒通道", "wake_not_configured")
    cfg.muted = body.muted
    await db.commit()
    bus = request.app.state.wake_bus
    return {
        "schema": 1,
        "wake": _config_dict(cfg, bus.is_connected(member.id)),
    }


@router.delete("/wake")
async def delete_wake(request: Request, db: AsyncSession = Depends(get_db)):
    member = await current_member(request, db)
    cfg = await db.get(WakeConfig, member.id)
    if cfg is not None:
        await db.delete(cfg)
        await db.commit()
    return {"schema": 1, "ok": True}


@router.post("/wake/lease")
async def acquire_lease(
    body: LeaseAcquire, request: Request, db: AsyncSession = Depends(get_db)
):
    """取得或续租 listener 处理权；同一 holder 续租保持 fence 不变。"""
    member = await current_member(request, db)
    if not HOLDER_RE.fullmatch(body.holder):
        raise bad_request("holder 只能包含字母/数字/_/-，长度 16-80", "invalid_holder")

    now = dt.datetime.now(dt.timezone.utc)
    expires = now + dt.timedelta(seconds=LEASE_TTL_SECONDS)
    base = pg_insert(ReceiverLease).values(
        member_id=member.id,
        holder=body.holder,
        fence=secrets.token_urlsafe(24),
        expires_at=expires,
    )
    stmt = (
        base.on_conflict_do_update(
            index_elements=[ReceiverLease.member_id],
            set_={
                "holder": base.excluded.holder,
                "fence": case(
                    (
                        and_(
                            ReceiverLease.holder == base.excluded.holder,
                            ReceiverLease.expires_at > now,
                        ),
                        ReceiverLease.fence,
                    ),
                    else_=base.excluded.fence,
                ),
                "expires_at": base.excluded.expires_at,
            },
            where=or_(
                ReceiverLease.expires_at <= now,
                ReceiverLease.holder == base.excluded.holder,
            ),
        )
        .returning(ReceiverLease.fence, ReceiverLease.expires_at)
    )
    row = (await db.execute(stmt)).first()
    await db.commit()
    if row is None:
        return {"schema": 1, "acquired": False}
    return {
        "schema": 1,
        "acquired": True,
        "fence": row.fence,
        "expiresAt": _iso(row.expires_at),
    }


@router.delete("/wake/lease")
async def release_lease(
    body: LeaseRelease, request: Request, db: AsyncSession = Depends(get_db)
):
    member = await current_member(request, db)
    result = await db.execute(
        delete(ReceiverLease).where(
            ReceiverLease.member_id == member.id,
            ReceiverLease.holder == body.holder,
            ReceiverLease.fence == body.fence,
        )
    )
    await db.commit()
    return {"schema": 1, "released": result.rowcount > 0}


@router.get("/wake/status")
async def wake_status(request: Request, db: AsyncSession = Depends(get_db)):
    """面向人的运行状态：最近队列与事件，供 CLI/Web 展示。"""
    member = await current_member(request, db)
    queue = (
        await db.execute(
            select(WakeQueue, Group.public_id)
            .join(Group, Group.id == WakeQueue.group_id)
            .where(WakeQueue.member_id == member.id)
            .order_by(WakeQueue.id.desc())
            .limit(20)
        )
    ).all()
    events = (
        await db.execute(
            select(WakeEvent)
            .where(WakeEvent.member_id == member.id)
            .order_by(WakeEvent.id.desc())
            .limit(20)
        )
    ).scalars().all()
    return {
        "schema": 1,
        "queue": [
            {
                "id": q.id,
                "groupId": group_public_id,
                "reason": q.reason,
                "urgent": bool(q.urgent),
                "status": q.status,
                "attempts": q.attempts,
                "nextAt": _iso(q.next_at),
                "createdAt": _iso(q.created_at),
            }
            for q, group_public_id in queue
        ],
        "events": [
            {
                "kind": e.kind,
                "result": e.result,
                "detail": e.detail,
                "at": _iso(e.created_at),
            }
            for e in events
        ],
    }


@router.post("/wake/test")
async def test_wake(request: Request, db: AsyncSession = Depends(get_db)):
    member = await current_member(request, db)
    cfg = await db.get(WakeConfig, member.id)
    if cfg is None:
        raise bad_request("尚未配置唤醒通道", "wake_not_configured")
    settings = request.app.state.settings
    if cfg.kind == "listener":
        connected = request.app.state.wake_bus.is_connected(member.id)
        return {
            "schema": 1,
            "test": {
                "ok": connected,
                "detail": "SSE 已连接" if connected else "当前没有活跃的 SSE 连接",
            },
        }
    ok, detail = await _test_webhook(settings, cfg)
    cfg.enabled = ok
    await db.commit()
    return {"schema": 1, "test": {"ok": ok, "detail": detail}}


@router.get("/wake/events")
async def wake_events(request: Request):
    async with request.app.state.sessionmaker() as db:
        member = await current_member(request, db)
        cfg = await db.get(WakeConfig, member.id)
        if cfg is None or cfg.kind != "listener":
            raise AppError("wake_not_listener", "当前唤醒通道不是 listener", 403)
        if cfg.muted:
            raise AppError("wake_muted", "已开启免打扰，不接收推送", 403)
        member_id = member.id

    settings = request.app.state.settings
    bus = request.app.state.wake_bus
    maker = request.app.state.sessionmaker
    raw_last = request.headers.get("Last-Event-ID") or request.query_params.get("lastEventId") or "0"
    try:
        last_id = int(raw_last)
    except ValueError:
        last_id = 0
    queue = bus.subscribe(member_id)

    async def fetch(after_id: int, include_done: bool):
        statuses = ["pending", "dispatching", "done"] if include_done else ["pending", "dispatching"]
        async with maker() as db:
            rows = (
                await db.execute(
                    select(WakeQueue, Group.public_id)
                    .join(Group, Group.id == WakeQueue.group_id)
                    .where(
                        WakeQueue.member_id == member_id,
                        WakeQueue.id > after_id,
                        WakeQueue.status.in_(statuses),
                    )
                    .order_by(WakeQueue.urgent.desc(), WakeQueue.id.asc())
                    .limit(200)
                )
            ).all()
            return [(r.id, wake_payload(r, gpid)) for r, gpid in rows]

    async def is_muted() -> bool:
        async with maker() as db:
            cfg = await db.get(WakeConfig, member_id)
        return bool(cfg is not None and cfg.muted)

    async def stream():
        sent = last_id
        last_ping = asyncio.get_running_loop().time()
        try:
            while True:
                # 免打扰在订阅后仍可能被打开；连接存续期间每轮复核，紧急也不穿透
                muted = await is_muted()
                if not muted:
                    for event_id, payload in await fetch(sent, include_done=last_id > 0):
                        yield f"id: {event_id}\nevent: wake\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        sent = max(sent, event_id)
                try:
                    event_id = await asyncio.wait_for(queue.get(), timeout=settings.sse_sweep_seconds)
                except asyncio.TimeoutError:
                    event_id = None
                if event_id is not None and event_id > sent and not muted:
                    async with maker() as db:
                        row = await db.get(WakeQueue, event_id)
                        group = (
                            await db.get(Group, row.group_id) if row is not None else None
                        )
                    if row is not None and row.member_id == member_id:
                        yield f"id: {row.id}\nevent: wake\ndata: {json.dumps(wake_payload(row, group.public_id if group else None), ensure_ascii=False)}\n\n"
                        sent = row.id
                now = asyncio.get_running_loop().time()
                if now - last_ping >= settings.sse_heartbeat_seconds:
                    last_ping = now
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(member_id, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
