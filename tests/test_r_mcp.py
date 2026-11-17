"""MCP（stdio）端到端：真实 Hub + `martin mcp` 子进程 + 官方客户端。"""
import asyncio
import json
import os
import socket
import sys
import threading
from pathlib import Path

import pytest
import pytest_asyncio
import uvicorn
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def live_server(engine):
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


async def test_T_R01_mcp_bootstrap_idempotent_and_read(live_server, fx, tmp_path):
    a = await fx.new_member("R-SEED-A", "R Bot A")
    g = await fx.create_group(a, "r-mcp")

    token_file = tmp_path / "mcp.token"
    token_file.write_text(a["token"])
    params = StdioServerParameters(
        command=str(Path(sys.executable).parent / "martin"),
        args=["mcp", "--token-file", str(token_file)],
        env={**os.environ, "MARTIN_URL": live_server},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert {
                "martin_bootstrap",
                "martin_probe",
                "martin_inbox",
                "martin_read_messages",
                "martin_send_message",
                "martin_recall_message",
                "martin_create_group",
                "martin_join_group",
                "martin_presence",
                "martin_wake",
            } <= names

            res = await session.call_tool("martin_bootstrap", {})
            data = json.loads(res.content[0].text)
            assert data["you"] == "R Bot A"
            assert isinstance(data.get("next_calls"), list) and data["next_calls"]

            res = await session.call_tool("martin_presence", {"status": "online"})
            presence = json.loads(res.content[0].text)
            assert presence["presence"] == "online"

            res = await session.call_tool(
                "martin_send_message",
                {"group_id": g["id"], "text": "from mcp", "op_key": "mcp-1"},
            )
            seq1 = json.loads(res.content[0].text)["seq"]

            res = await session.call_tool(
                "martin_send_message",
                {"group_id": g["id"], "text": "from mcp", "op_key": "mcp-1"},
            )
            replay = json.loads(res.content[0].text)
            assert replay["idempotent"] is True and replay["seq"] == seq1

            res = await session.call_tool("martin_read_messages", {"group_id": g["id"]})
            msgs = json.loads(res.content[0].text)["messages"]
            assert any(m["seq"] == seq1 and m["text"] == "from mcp" for m in msgs)
