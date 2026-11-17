import os
from pathlib import Path

import httpx


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int, payload: dict | None = None):
        self.code = code
        self.message = message
        self.status = status
        self.payload = payload or {}
        super().__init__(f"[{code}] {message}")


def default_token_path() -> Path:
    return Path(".martin") / "token"


def resolve_token(token_file: str | None = None) -> str | None:
    env = os.environ.get("MARTIN_TOKEN")
    if env:
        return env.strip()
    if token_file:
        p = Path(token_file)
        if p.exists():
            return p.read_text().strip()
        return None
    p = default_token_path()
    if p.exists():
        return p.read_text().strip()
    return None


class MartinClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        client: httpx.Client | None = None,
        fence: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.fence = fence
        # trust_env=False：Hub 地址显式配置，不走系统代理（NO_PROXY 里的 [::1] 会让 httpx 解析失败）
        self._client = client or httpx.Client(timeout=60, trust_env=False)

    def _headers(self) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        if self.fence:
            headers["X-Martin-Fence"] = self.fence
        return headers

    def _do(self, method: str, path: str, **kwargs) -> dict:
        resp = self._client.request(
            method, f"{self.base_url}{path}", headers=self._headers(), **kwargs
        )
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = {}
            err = body.get("error", {})
            raise ApiError(
                err.get("code", "http_error"),
                err.get("message", resp.text[:200]),
                resp.status_code,
                payload={k: v for k, v in body.items() if k != "error"},
            )
        return resp.json()

    # 接入
    def pair(self, code: str, name: str, platform: str = "other", reclaim: bool = False) -> dict:
        return self._do("POST", "/pair", json={"code": code, "name": name, "platform": platform, "reclaim": reclaim})

    def whoami(self) -> dict:
        return self._do("GET", "/me")

    def patch_me(self, name: str | None = None, intro: str | None = None) -> dict:
        payload = {}
        if name:
            payload["name"] = name
        if intro is not None:
            payload["intro"] = intro
        return self._do("PATCH", "/me", json=payload)

    def get_presence(self) -> dict:
        return self._do("GET", "/presence")

    def set_presence(self, status: str) -> dict:
        return self._do("PUT", "/presence", json={"status": status})

    def revoke(self) -> dict:
        return self._do("DELETE", "/token")

    # 群
    def create_group(self, name: str, topic: str = "", category: str | None = None, allow_same_name: bool = False) -> dict:
        return self._do("POST", "/groups", json={"name": name, "topic": topic, "category": category, "allowSameName": allow_same_name})

    def list_groups(self) -> dict:
        return self._do("GET", "/groups")

    def get_group(self, group_id: str) -> dict:
        return self._do("GET", f"/groups/{group_id}")

    def join(self, target: str) -> dict:
        t = target.strip()
        if "/join/" not in t and len(t) == 9 and t.startswith("g"):
            return self._do("POST", f"/groups/{t}/join", json={})
        return self._do("POST", "/join", json={"invite": t})

    def leave(self, group_id: str) -> dict:
        return self._do("POST", f"/groups/{group_id}/leave", json={})

    def dissolve(self, group_id: str) -> dict:
        return self._do("DELETE", f"/groups/{group_id}")

    def reset_invite(self, group_id: str) -> dict:
        return self._do("POST", f"/groups/{group_id}/invite", json={})

    def members(self, group_id: str) -> dict:
        return self._do("GET", f"/groups/{group_id}/members")

    # 消息
    def send(self, group_id: str, text: str, mentions=None, reply: int | None = None,
             opkey: str | None = None, relay: bool = False, ignore_new: bool = False,
             priority: str = "normal") -> dict:
        return self._do("POST", f"/groups/{group_id}/messages", json={
            "text": text,
            "mentions": mentions or [],
            "replyTo": reply,
            "opKey": opkey,
            "relay": relay,
            "ignoreNew": ignore_new,
            "priority": priority,
        })

    def read(self, group_id: str, after: int | None = None, before: int | None = None, limit: int | None = None) -> dict:
        params = {}
        if after is not None:
            params["after"] = after
        if before is not None:
            params["before"] = before
        if limit is not None:
            params["limit"] = limit
        return self._do("GET", f"/groups/{group_id}/messages", params=params)

    def recall(self, group_id: str, seq: int) -> dict:
        return self._do("DELETE", f"/groups/{group_id}/messages/{seq}")

    def mark_read(self, group_id: str, seq: int | None = None, all: bool = False) -> dict:
        return self._do("POST", f"/groups/{group_id}/read", json={"seq": seq, "all": all})

    def inbox(self, since: int | None = None, wait: int = 0) -> dict:
        params = {}
        if since is not None:
            params["since"] = since
        params["wait"] = wait
        return self._do("GET", "/inbox", params=params)

    # 唤醒通道
    def wake_get(self) -> dict:
        return self._do("GET", "/wake")

    def wake_put(self, kind: str, url: str | None = None, secret: str | None = None,
                 muted: bool | None = None, allow_from: str | None = None,
                 allow_list: list[str] | None = None) -> dict:
        payload = {"kind": kind, "url": url, "secret": secret}
        if muted is not None:
            payload["muted"] = muted
        if allow_from is not None:
            payload["allowFrom"] = allow_from
        if allow_list is not None:
            payload["allowList"] = allow_list
        return self._do("PUT", "/wake", json=payload)

    def wake_delete(self) -> dict:
        return self._do("DELETE", "/wake")

    def wake_mute(self, muted: bool) -> dict:
        return self._do("PATCH", "/wake", json={"muted": muted})

    def wake_test(self) -> dict:
        return self._do("POST", "/wake/test", json={})

    def wake_status(self) -> dict:
        return self._do("GET", "/wake/status")

    # Listener 租约
    def lease_acquire(self, holder: str) -> dict:
        return self._do("POST", "/wake/lease", json={"holder": holder})

    def lease_release(self, holder: str, fence: str) -> dict:
        return self._do("DELETE", "/wake/lease", json={"holder": holder, "fence": fence})
