from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from martin.client import ApiError, MartinClient


class HubError(Exception):
    pass


class Hub:
    def __init__(self, base_url: str, token: str):
        base = base_url.rstrip("/")
        if not base.endswith("/api/v1"):
            base = f"{base}/api/v1"
        self.base_url = base
        self.token = token
        self.fence: str | None = None
        self.client = MartinClient(self.base_url, token=token)

    # ---- wake channel ----------------------------------------------------
    def ensure_wake_channel(self, auto_configure: bool = True) -> str:
        try:
            state = self.client.wake_get()
        except (ApiError, httpx.HTTPError) as exc:
            raise HubError(f"cannot read wake channel: {exc}") from exc
        wake = state.get("wake")
        if wake is None:
            if not auto_configure:
                raise HubError("wake channel is not configured; run `martin wake --listener`")
            try:
                self.client.wake_put("listener")
            except (ApiError, httpx.HTTPError) as exc:
                raise HubError(f"cannot configure listener channel: {exc}") from exc
            return "listener"
        kind = wake.get("kind")
        if kind != "listener":
            raise HubError(f"wake channel is {kind!r}, runner needs 'listener'")
        return "listener"

    # ---- lease -----------------------------------------------------------
    def acquire_lease(self, holder: str) -> str:
        try:
            lease = self.client.lease_acquire(holder)
        except (ApiError, httpx.HTTPError) as exc:
            raise HubError(f"cannot acquire listener lease: {exc}") from exc
        if not lease.get("acquired"):
            raise HubError("another listener holds this member's lease")
        self.fence = str(lease["fence"])
        return self.fence

    def renew_lease(self, holder: str, fence: str) -> bool:
        try:
            lease = self.client.lease_acquire(holder)
        except (ApiError, httpx.HTTPError) as exc:
            raise HubError(f"lease renew failed: {exc}") from exc
        return bool(lease.get("acquired")) and str(lease.get("fence")) == fence

    def release_lease(self, holder: str, fence: str) -> None:
        try:
            self.client.lease_release(holder, fence)
        except (ApiError, httpx.HTTPError):
            pass
        self.fence = None

    # ---- messages --------------------------------------------------------
    def fetch(self, group_id: str, cursor: int | None) -> tuple[list[dict], int | None]:
        try:
            if cursor is None:
                page = self.client.read(group_id, limit=50)
            else:
                page = self.client.read(group_id, after=cursor, limit=50)
        except (ApiError, httpx.HTTPError) as exc:
            raise HubError(f"cannot read group {group_id}: {exc}") from exc
        messages = list(page.get("messages") or [])
        max_seq = max((int(item.get("seq") or 0) for item in messages), default=0) or None
        return messages, max_seq

    def mark_read(self, group_id: str, seq: int) -> None:
        try:
            self.client.mark_read(group_id, seq=seq)
        except (ApiError, httpx.HTTPError) as exc:
            raise HubError(f"cannot mark {group_id} read: {exc}") from exc

    def send(self, group_id: str, text: str, opkey: str) -> dict:
        try:
            return self.client.send(group_id, text, opkey=opkey)
        except (ApiError, httpx.HTTPError) as exc:
            raise HubError(f"cannot send to {group_id}: {exc}") from exc

    # ---- SSE -------------------------------------------------------------
    def stream(self, last_event_id: int = 0) -> Iterator[tuple[int, dict]]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "text/event-stream",
        }
        if self.fence:
            headers["X-Martin-Fence"] = self.fence
        if last_event_id:
            headers["Last-Event-ID"] = str(last_event_id)
        try:
            with httpx.Client(timeout=None, trust_env=False) as client:
                with client.stream("GET", f"{self.base_url}/wake/events", headers=headers) as response:
                    if response.status_code != 200:
                        body = response.read().decode("utf-8", "replace")[:300]
                        raise HubError(f"wake stream HTTP {response.status_code}: {body}")
                    event_id = last_event_id
                    data_lines: list[str] = []
                    for line in response.iter_lines():
                        line = line.rstrip("\r")
                        if not line:
                            if data_lines:
                                try:
                                    payload = json.loads("\n".join(data_lines))
                                except json.JSONDecodeError:
                                    payload = {}
                                if payload:
                                    yield event_id, payload
                            data_lines = []
                            continue
                        if line.startswith(":"):
                            continue
                        if line.startswith("id:"):
                            try:
                                event_id = int(line[3:].strip())
                            except ValueError:
                                pass
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
        except httpx.HTTPError as exc:
            raise HubError(f"wake stream disconnected: {type(exc).__name__}") from exc
