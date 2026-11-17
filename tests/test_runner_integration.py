from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from martin_runner.config import load_config
from martin_runner.runner import Runner


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
session = None
if "--session-id" in args:
    session = args[args.index("--session-id") + 1]
elif "--resume" in args:
    session = args[args.index("--resume") + 1]
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as fh:
    fh.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\n")
prompt = sys.stdin.read()
print(json.dumps({"type": "system", "subtype": "init", "session_id": session}))
print(json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "session_id": session,
    "result": "fake reply: " + prompt.strip().splitlines()[-1],
    "total_cost_usd": 0.0,
    "duration_ms": 1,
}))
'''


class FakeHub:
    def __init__(self):
        self.posted: list[dict] = []
        self.requests: list[str] = []
        self.stop_sse = threading.Event()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, payload, status=200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.requests.append(self.path)
                if self.path == "/api/v1/wake":
                    self._json({"schema": 1, "wake": {"kind": "listener", "connected": True}})
                    return
                if self.path == "/api/v1/wake/events":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(
                        b'id: 7\nevent: wake\ndata: {"kind":"wake","groupId":"g1","reason":"mention","urgent":false}\n\n'
                    )
                    self.wfile.flush()
                    while not outer.stop_sse.wait(0.1):
                        pass
                    return
                if self.path.startswith("/api/v1/groups/g1/messages"):
                    self._json(
                        {
                            "schema": 1,
                            "cursor": 3,
                            "messages": [
                                {"seq": 3, "author": {"name": "Alice"}, "text": "hello bridge"}
                            ],
                        }
                    )
                    return
                self._json({"error": {"code": "not_found"}}, 404)

            def do_POST(self):
                outer.requests.append(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/v1/wake/lease":
                    self._json({"schema": 1, "acquired": True, "fence": "fence-test-1234"})
                    return
                if self.path.endswith("/read"):
                    self._json({"schema": 1, "ok": True})
                    return
                if self.path == "/api/v1/groups/g1/messages":
                    outer.posted.append(body)
                    self._json({"schema": 1, "message": {"seq": 4}})
                    return
                self._json({"error": {"code": "not_found"}}, 404)

            def do_DELETE(self):
                outer.requests.append(self.path)
                self._json({"schema": 1, "released": True})

        return Handler

    def start(self):
        self.thread.start()

    def close(self):
        self.stop_sse.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake_claude(tmp_path: Path) -> Path:
    path = tmp_path / "fake-claude"
    path.write_text(FAKE_CLAUDE, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_runner_processes_one_wake(tmp_path: Path, fake_claude: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".martin").mkdir()
    (workspace / ".martin" / "token").write_text("test-token", encoding="utf-8")
    claude_config = tmp_path / "claude.json"
    claude_config.write_text(
        json.dumps({"projects": {str(workspace): {"hasTrustDialogAccepted": True}}}),
        encoding="utf-8",
    )
    log_file = tmp_path / "fake-claude.log"

    hub = FakeHub()
    hub.start()

    config_path = tmp_path / "runner.toml"
    config_path.write_text(
        f"""
[hub]
base_url = "{hub.base_url}"
token = "test-token"

[runner]
workspace = "{workspace}"
state_dir = "{tmp_path / 'state'}"

[claude]
command = "{fake_claude}"
permission_mode = "acceptEdits"
timeout_seconds = 60
config_file = "{claude_config}"
""".strip(),
        encoding="utf-8",
    )
    os.environ["FAKE_CLAUDE_LOG"] = str(log_file)
    config = load_config(config_path)
    runner = Runner(config)
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not hub.posted and time.time() < deadline:
            time.sleep(0.05)
        assert hub.posted, "runner did not post a reply"
        body = hub.posted[0]
        assert body["opKey"] == "runner:7"
        assert body["text"].startswith("fake reply:")
        session = runner.store.get_session("g1")
        assert session is not None
        assert session.cwd == str(workspace)
        assert config.token() == "test-token"
    finally:
        runner.stop()
        hub.close()
        thread.join(timeout=10)
