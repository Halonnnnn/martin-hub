import asyncio
import json
import socket
import threading

import pytest_asyncio
from typer.testing import CliRunner

from martin.client import resolve_token
from martin.cli import app as cli_app

runner = CliRunner()


@pytest_asyncio.fixture
async def server(engine):
    import uvicorn

    from martin.main import create_app

    from tests.conftest import make_settings

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    application = create_app(make_settings(), ensure_schema=False)
    config = uvicorn.Config(application, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        await asyncio.sleep(0.05)
    assert srv.started, "uvicorn 未能在时限内启动"
    yield f"http://127.0.0.1:{port}/api/v1"
    srv.should_exit = True
    thread.join(timeout=5)


async def test_T_K01_cli_smoke(fx, db, server, tmp_path):
    from tests.conftest import Fixtures  # noqa: F401

    owner = await fx.make_owner("cliowner")
    ta = str(tmp_path / "ta.token")
    tb = str(tmp_path / "tb.token")
    env = {"MARTIN_URL": server}

    await fx.make_code("CLI-CODA", owner_id=owner.id)
    r = runner.invoke(cli_app, ["pair", "CLI-CODA", "--name", "Cli Bot", "--platform", "codex", "--token-file", ta], env=env)
    assert r.exit_code == 0, r.output
    assert "已接入为 Cli Bot" in r.output

    r = runner.invoke(cli_app, ["whoami", "--token-file", ta], env=env)
    assert r.exit_code == 0
    assert "Cli Bot" in r.output

    r = runner.invoke(cli_app, ["presence", "online", "--token-file", ta], env=env)
    assert r.exit_code == 0, r.output
    assert "已上线" in r.output
    r = runner.invoke(cli_app, ["presence", "--token-file", ta], env=env)
    assert r.exit_code == 0, r.output
    assert "当前状态：online" in r.output

    r = runner.invoke(cli_app, ["create", "--name", "cli-group", "--token-file", ta, "--json"], env=env)
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    gid = payload["group"]["id"]
    link = payload["group"]["inviteLink"]

    await fx.make_code("CLI-CODB")
    r = runner.invoke(cli_app, ["pair", "CLI-CODB", "--name", "Cli Two", "--token-file", tb], env=env)
    assert r.exit_code == 0

    r = runner.invoke(cli_app, ["join", link, "--token-file", tb], env=env)
    assert r.exit_code == 0, r.output

    r = runner.invoke(cli_app, ["send", str(gid), "@Cli Two 你好", "--mention", "Cli Two", "--token-file", ta], env=env)
    assert r.exit_code == 0, r.output
    assert "已发送 seq=1" in r.output

    r = runner.invoke(cli_app, ["read", str(gid), "--token-file", tb], env=env)
    assert r.exit_code == 0
    assert "你好" in r.output

    r = runner.invoke(cli_app, ["inbox", "--token-file", tb], env=env)
    assert r.exit_code == 0
    assert "cli-group" in r.output

    r = runner.invoke(cli_app, ["recall", str(gid), "1", "--token-file", ta], env=env)
    assert r.exit_code == 0
    assert "已撤回 seq=1" in r.output

    r = runner.invoke(cli_app, ["read", str(gid), "--before", "2", "--token-file", tb], env=env)
    assert r.exit_code == 0
    assert "已撤回" in r.output


async def test_T_K03_cli_json_output(fx, server, tmp_path):
    ta = str(tmp_path / "tc.token")
    env = {"MARTIN_URL": server}
    await fx.make_code("K03-COD1")
    r = runner.invoke(cli_app, ["pair", "K03-COD1", "--name", "Json Bot", "--token-file", ta], env=env)
    assert r.exit_code == 0
    r = runner.invoke(cli_app, ["whoami", "--token-file", ta, "--json"], env=env)
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert data["member"]["name"] == "Json Bot"


def test_T_K02_token_resolution_order(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.chdir(ws)
    tf = tmp_path / "file.token"
    tf.write_text("file-token\n")
    default_dir = ws / ".martin"
    default_dir.mkdir(parents=True)
    (default_dir / "token").write_text("default-token")

    monkeypatch.delenv("MARTIN_TOKEN", raising=False)
    from pathlib import Path

    from martin.client import default_token_path

    assert default_token_path() == Path(".martin") / "token"

    assert resolve_token(None) == "default-token"
    assert resolve_token(str(tf)) == "file-token"

    monkeypatch.setenv("MARTIN_TOKEN", "env-token")
    assert resolve_token(None) == "env-token"
    assert resolve_token(str(tf)) == "env-token"

    monkeypatch.delenv("MARTIN_TOKEN")
    missing = tmp_path / "nope.token"
    assert resolve_token(str(missing)) is None


def test_T_Q09_listener_releases_lease_on_interrupt(tmp_path, monkeypatch):
    """Ctrl-C（KeyboardInterrupt）退出时必须释放租约并清理本地 lease.json。"""
    import httpx

    from martin import cli as cli_mod

    calls = {}

    class FakeClient:
        def __init__(self, base_url, token=None, client=None, fence=None):
            pass

        def lease_acquire(self, holder):
            calls["holder"] = holder
            return {"acquired": True, "fence": "fence-1", "expiresAt": "2999-01-01T00:00:00+00:00"}

        def lease_release(self, holder, fence):
            calls["release"] = (holder, fence)
            return {"released": True}

    class FakeHTTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_mod, "MartinClient", FakeClient)
    monkeypatch.setattr(cli_mod, "resolve_token", lambda tf: "tok")
    monkeypatch.setattr(cli_mod, "_base_url", lambda: "http://x/api/v1")
    monkeypatch.setattr(httpx, "Client", FakeHTTP)

    token_file = str(tmp_path / "token")
    cli_mod._listen_events(token_file, False)

    assert calls["release"] == (calls["holder"], "fence-1")
    assert not (tmp_path / "lease.json").exists()
