import asyncio
import contextlib
import datetime as dt
import logging

import httpx
from sqlalchemy import select

from .models import Group, WakeConfig, WakeEvent, WakeQueue
from .security import decrypt_secret

log = logging.getLogger(__name__)

BATCH_SIZE = 50


def wake_payload(row: WakeQueue, group_public_id: str | None) -> dict:
    """推送载荷只带定位信息，不含消息正文与凭证。"""
    return {
        "schema": 1,
        "kind": "wake",
        "groupId": group_public_id,
        "reason": row.reason,
        "urgent": bool(row.urgent),
        "at": row.created_at.isoformat() if row.created_at else None,
    }


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Dispatcher:
    def __init__(self, settings, sessionmaker, cache, bus):
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.cache = cache
        self.bus = bus
        self._loop_task: asyncio.Task | None = None
        self._kick_task: asyncio.Task | None = None
        self._stopped = False

    def start(self) -> None:
        if not self.settings.wake_dispatch_enabled:
            return
        self._stopped = False
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopped = True
        for task in (self._loop_task, self._kick_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._loop_task = None
        self._kick_task = None

    def kick(self) -> None:
        if not self.settings.wake_dispatch_enabled:
            return
        if self._kick_task is None or self._kick_task.done():
            self._kick_task = asyncio.create_task(self._safe_run())

    async def _loop(self) -> None:
        await self._recover()
        while not self._stopped:
            await self._safe_run()
            await asyncio.sleep(self.settings.wake_dispatch_interval_seconds)

    async def _safe_run(self) -> None:
        try:
            await self.run_once()
        except Exception:
            log.exception("wake dispatch failed")

    async def _recover(self) -> None:
        """结算上一个进程遗留的 dispatching。

        listener 重投只是提示，SSE 侧按事件 id 去重；webhook 可能已经发出，
        重发会二次触发外部工作，因此结为 ambiguous，不自动重试。
        """
        async with self.sessionmaker() as db:
            rows = (
                await db.execute(
                    select(WakeQueue).where(WakeQueue.status == "dispatching")
                )
            ).scalars().all()
            if not rows:
                return
            for row in rows:
                cfg = await db.get(WakeConfig, row.member_id)
                if cfg is not None and cfg.kind == "webhook":
                    row.status = "ambiguous"
                    db.add(
                        WakeEvent(
                            member_id=row.member_id,
                            group_id=row.group_id,
                            reason=row.reason,
                            urgent=bool(row.urgent),
                            kind="webhook",
                            result="ambiguous",
                            detail="recovered after restart",
                        )
                    )
                else:
                    row.status = "pending"
            await db.commit()

    async def run_once(self) -> int:
        async with self.sessionmaker() as db:
            rows = (
                await db.execute(
                    select(WakeQueue)
                    .where(
                        WakeQueue.status == "pending",
                        WakeQueue.next_at <= _now(),
                    )
                    .order_by(WakeQueue.urgent.desc(), WakeQueue.next_at.asc())
                    .limit(BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            if not rows:
                return 0
            for row in rows:
                row.status = "dispatching"
            await db.commit()
            for row in rows:
                await self._dispatch_one(db, row)
            return len(rows)

    async def _dispatch_one(self, db, row: WakeQueue) -> None:
        cfg = await db.get(WakeConfig, row.member_id)
        if cfg is not None and cfg.muted:
            await self._finish(db, row, "suppressed", cfg.kind, "muted")
            return
        if cfg is None or not cfg.enabled:
            await self._finish(db, row, "undeliverable", cfg.kind if cfg else None, "no active push channel")
            return

        if not row.urgent:
            if row.reason != "owner_post":
                dedup_key = f"martin:wake:dedup:{row.group_id}:{row.member_id}"
                if not await self.cache.set_if_absent(
                    dedup_key, "1", self.settings.wake_dedup_seconds
                ):
                    await self._finish(db, row, "suppressed", cfg.kind, "dedup")
                    return
            day = _now().strftime("%Y%m%d")
            count = await self.cache.incr(f"martin:wake:daily:{row.member_id}:{day}", 86400)
            if count > self.settings.wake_daily_limit:
                await self._finish(db, row, "suppressed", cfg.kind, "daily_limit")
                return

        if cfg.kind == "listener":
            if self.bus.is_connected(row.member_id):
                self.bus.publish(row.member_id, row.id)
                await self._finish(db, row, "done", "listener", "")
            else:
                row.status = "pending"
                row.next_at = _now() + dt.timedelta(seconds=self.settings.sse_sweep_seconds)
                await db.commit()
            return

        await self._webhook(db, row, cfg)

    async def _webhook(self, db, row: WakeQueue, cfg: WakeConfig) -> None:
        secret = (
            decrypt_secret(self.settings.secret_key, cfg.secret_cipher)
            if cfg.secret_cipher
            else None
        )
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        ok = False
        group = await db.get(Group, row.group_id)
        payload = wake_payload(row, group.public_id if group is not None else None)
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.webhook_timeout_seconds, trust_env=False
            ) as client:
                resp = await client.post(cfg.url, json=payload, headers=headers)
            ok = 200 <= resp.status_code < 300
            detail = f"http {resp.status_code}"
        except Exception as exc:
            detail = f"{type(exc).__name__}"[:200]

        if ok:
            await self._finish(db, row, "done", "webhook", detail)
            return
        if row.attempts < self.settings.wake_retry_max:
            row.attempts += 1
            row.status = "pending"
            row.next_at = _now() + dt.timedelta(seconds=self.settings.wake_retry_delay_seconds)
            await db.commit()
            await self._event(db, row, "webhook", "retry", detail)
            return
        await self._finish(db, row, "failed", "webhook", detail)

    async def _finish(self, db, row: WakeQueue, status: str, kind: str | None, detail: str) -> None:
        row.status = status
        await db.commit()
        await self._event(db, row, kind, status, detail)

    async def _event(self, db, row: WakeQueue, kind: str | None, result: str, detail: str) -> None:
        db.add(
            WakeEvent(
                member_id=row.member_id,
                group_id=row.group_id,
                reason=row.reason,
                urgent=bool(row.urgent),
                kind=kind,
                result=result,
                detail=detail,
            )
        )
        await db.commit()
