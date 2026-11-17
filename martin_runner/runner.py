from __future__ import annotations

import logging
import os
import queue
import re
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .claude import ClaudeDriver, ClaudeError, now_iso
from .config import Config
from .hub import Hub, HubError
from .store import Store

log = logging.getLogger("martin-runner")


@dataclass
class Wake:
    event_id: int
    group_id: str
    reason: str
    urgent: bool


def _holder(config: Config) -> str:
    value = config.runner.lease_holder.strip()
    if value:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", value):
            raise ValueError("runner.lease_holder must match [A-Za-z0-9_-]{16,80}")
        return value
    raw = f"runner-{socket.gethostname()}-{os.getpid()}"
    value = re.sub(r"[^A-Za-z0-9_-]", "-", raw)[:80]
    while len(value) < 16:
        value += "-x"
    return value


def build_prompt(
    *,
    group_id: str,
    reason: str,
    urgent: bool,
    messages: list[dict[str, Any]],
    notes: str = "",
) -> str:
    lines: list[str] = []
    if notes.strip():
        lines += ["【用户对该 agent 的长期要求】", notes.strip(), ""]
    lines.append(
        f"【Martin 唤醒】群 {group_id} 有 {len(messages)} 条未读消息"
        f"（reason={reason}{', urgent' if urgent else ''}）。"
    )
    for message in messages[-40:]:
        author = (message.get("author") or {}).get("name") or "unknown"
        text = re.sub(r"\s+", " ", str(message.get("text") or "")).strip()
        lines.append(f"- [{message.get('seq')}] {author}: {text[:2000]}")
    lines += [
        "",
        "请处理这些消息，并在本轮最后给出要发回群里的回复。",
        "只输出回复本身；回复由 runner 自动发回群，不要调用 Martin 的发送工具。",
    ]
    prompt = "\n".join(lines)
    return prompt[:16000]


class Runner:
    def __init__(self, config: Config):
        self.config = config
        self.workspace = os.path.realpath(config.runner.workspace)
        config.runner.workspace = self.workspace
        config.check()
        config.ensure_dirs()
        self.store = Store(config.state_dir / "state.db")
        self.hub = Hub(config.hub.base_url, config.token())
        self.driver = ClaudeDriver(config.claude, self.workspace)
        self.holder = _holder(config)
        self.stop_event = threading.Event()
        self.queue: queue.Queue[Wake] = queue.Queue()
        self.seen: set[int] = set()
        self.last_event_id = int(self.store.get_kv("last_event_id") or 0)
        self.fence = ""
        self._worker: threading.Thread | None = None
        self._renew: threading.Thread | None = None

    # ---- lifecycle -------------------------------------------------------
    def run(self) -> None:
        log.info("workspace=%s hub=%s", self.workspace, self.config.hub.base_url)
        log.info("claude=%s", self.driver.version())
        self.hub.ensure_wake_channel(self.config.hub.auto_configure_wake)
        self.fence = self.hub.acquire_lease(self.holder)
        log.info("lease acquired holder=%s fence=%s", self.holder, self.fence)

        self._worker = threading.Thread(target=self._work, name="martin-worker", daemon=True)
        self._worker.start()
        self._renew = threading.Thread(target=self._renew_lease, name="martin-lease", daemon=True)
        self._renew.start()

        try:
            self._stream_loop()
        finally:
            self.stop_event.set()
            self.queue.put(Wake(0, "", "stop", False))
            if self._worker:
                self._worker.join(timeout=30)
            self.hub.release_lease(self.holder, self.fence)
            self.store.close()
            log.info("runner stopped")

    def stop(self) -> None:
        self.stop_event.set()

    def _renew_lease(self) -> None:
        while not self.stop_event.wait(30):
            try:
                if not self.hub.renew_lease(self.holder, self.fence):
                    log.error("listener lease lost; stopping")
                    self.stop_event.set()
                    return
            except HubError as exc:
                log.warning("lease renew failed: %s", exc)
        try:
            self.hub.release_lease(self.holder, self.fence)
        except Exception:
            pass

    def _stream_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                for event_id, payload in self.hub.stream(self.last_event_id):
                    if self.stop_event.is_set():
                        return
                    self._enqueue(event_id, payload)
            except HubError as exc:
                if self.stop_event.is_set():
                    return
                log.warning("wake stream error: %s", exc)
                wait = 30 if "401" in str(exc) or "403" in str(exc) else 3
                self.stop_event.wait(wait)

    def _enqueue(self, event_id: int, payload: dict[str, Any]) -> None:
        if payload.get("kind") != "wake":
            return
        if event_id in self.seen:
            return
        group_id = str(payload.get("groupId") or "")
        if not group_id:
            return
        allow = self.config.hub.groups
        if allow and group_id not in allow:
            return
        if event_id in self.seen:
            return
        self.seen.add(event_id)
        if len(self.seen) > 2000:
            self.seen = {item for item in self.seen if item > event_id - 500}
        self.queue.put(
            Wake(
                event_id=event_id,
                group_id=group_id,
                reason=str(payload.get("reason") or ""),
                urgent=bool(payload.get("urgent")),
            )
        )
        log.info("queued wake %s group=%s reason=%s", event_id, group_id, payload.get("reason"))

    # ---- worker ----------------------------------------------------------
    def _work(self) -> None:
        while not self.stop_event.is_set():
            try:
                wake = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            if wake.reason == "stop" and not wake.group_id:
                return
            try:
                self._process(wake)
            except Exception:
                log.exception("wake %s failed", wake.event_id)
            finally:
                self.queue.task_done()

    def _process(self, wake: Wake) -> None:
        started = time.time()
        pending = self.store.get_pending(wake.group_id)
        if pending:
            messages, max_seq = pending
            log.info("wake %s group=%s: retrying %s pending messages", wake.event_id, wake.group_id, len(messages))
        else:
            cursor = self.store.get_cursor(wake.group_id)
            messages, max_seq = self.hub.fetch(wake.group_id, cursor)
            if messages and max_seq:
                self.store.set_pending(wake.group_id, messages, max_seq, wake.reason)
        if not messages:
            log.info("wake %s group=%s: no new messages", wake.event_id, wake.group_id)
            self.last_event_id = max(self.last_event_id, wake.event_id)
            self.store.set_kv("last_event_id", str(self.last_event_id))
            return

        session = self.store.get_session(wake.group_id)
        session_id = session.session_id if session and session.cwd == self.workspace else None
        if session and session.cwd != self.workspace:
            log.warning("session %s cwd changed; starting a new one", session.session_id)
            self.store.delete_session(wake.group_id)

        notes = self._notes()
        prompt = build_prompt(
            group_id=wake.group_id,
            reason=wake.reason,
            urgent=wake.urgent,
            messages=messages,
            notes=notes,
        )
        log.info(
            "wake %s group=%s messages=%s session=%s",
            wake.event_id,
            wake.group_id,
            len(messages),
            session_id or "new",
        )
        try:
            result = self.driver.run(prompt, session_id)
        except ClaudeError as exc:
            self.store.record_run(
                wake_id=wake.event_id,
                group_id=wake.group_id,
                session_id=session_id,
                started_at=now_iso(),
                status="failed",
                detail=str(exc),
            )
            log.error("wake %s claude failed: %s", wake.event_id, exc)
            return

        self.store.save_session(wake.group_id, "claude", result.session_id, self.workspace)
        reply = result.text.strip()
        if reply:
            try:
                self.hub.send(
                    wake.group_id,
                    reply[:9000],
                    opkey=f"runner:{wake.event_id}",
                )
            except HubError as exc:
                self.store.record_run(
                    wake_id=wake.event_id,
                    group_id=wake.group_id,
                    session_id=result.session_id,
                    started_at=now_iso(),
                    status="send_failed",
                    detail=str(exc),
                )
                log.error("wake %s reply failed: %s", wake.event_id, exc)
                return

        if max_seq:
            self.store.set_cursor(wake.group_id, max_seq)
            try:
                self.hub.mark_read(wake.group_id, max_seq)
            except HubError as exc:
                log.warning("mark read failed: %s", exc)
        self.last_event_id = max(self.last_event_id, wake.event_id)
        self.store.set_kv("last_event_id", str(self.last_event_id))
        self.store.delete_pending(wake.group_id)
        self.store.record_run(
            wake_id=wake.event_id,
            group_id=wake.group_id,
            session_id=result.session_id,
            started_at=now_iso(),
            status="ok",
            detail=f"cost={result.cost_usd} duration_ms={result.duration_ms}",
        )
        log.info(
            "wake %s replied group=%s chars=%s cost=%s duration=%sms",
            wake.event_id,
            wake.group_id,
            len(reply),
            result.cost_usd,
            result.duration_ms,
        )

    def _notes(self) -> str:
        candidates = []
        if self.config.runner.notes_file:
            candidates.append(Path(os.path.expanduser(self.config.runner.notes_file)))
        candidates.append(Path(self.workspace) / ".martin" / "notes.md")
        for path in candidates:
            try:
                return path.read_text(encoding="utf-8")[:6000]
            except OSError:
                continue
        return ""
