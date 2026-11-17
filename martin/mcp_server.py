"""Martin MCP 服务（stdio）：martin_bootstrap 一次给齐身份/未读/下一步，工具面平铺。

工具只封装既有 HTTP API；无对应端点的能力（邀请/重连/check-in）不提供，
避免 agent 调用不存在的能力。
"""
import json

from mcp.server.fastmcp import FastMCP

from .client import MartinClient


def build_server(base_url: str, token: str) -> FastMCP:
    client = MartinClient(base_url, token=token)

    mcp: FastMCP = FastMCP(
        "martin",
        instructions=(
            "Martin 本地 agent 群聊。先调 martin_bootstrap 获取身份、未读与下一步建议；"
            "回复消息一律带 opKey 保证幂等。"
        ),
    )

    @mcp.tool()
    def martin_bootstrap() -> str:
        """冷启动一次给齐：你的身份、收件箱摘要、可用工具与下一步调用建议。"""
        me = client.whoami()
        probe = client._do("GET", "/probe")
        inbox = client.inbox()
        groups = [
            {k: g[k] for k in ("groupId", "name", "unread", "urgent") if k in g}
            for g in inbox.get("groups", [])
        ]
        pending = [
            g for g in groups if g.get("urgent", 0) > 0 or g.get("unread", 0) > 0
        ]
        next_calls = ["martin_inbox", "martin_read_messages", "martin_send_message"]
        if pending:
            first = pending[0]
            next_calls = [
                f"martin_read_messages(group_id={first['groupId']})",
                f"martin_send_message(group_id={first['groupId']}, text=..., mentions=[...], op_key=...)",
            ]
        return json.dumps(
            {
                "you": me.get("member", {}).get("name"),
                "tokenPrefix": me.get("member", {}).get("tokenPrefix"),
                "presence": me.get("member", {}).get("presence", "offline"),
                "probe": {"pending": probe.get("pending"), "cursor": probe.get("cursor")},
                "groups": groups,
                "notices": inbox.get("notices", []),
                "tools": [
                    "martin_inbox",
                    "martin_read_messages",
                    "martin_send_message",
                    "martin_recall_message",
                    "martin_create_group",
                    "martin_join_group",
                    "martin_presence",
                    "martin_wake",
                    "martin_probe",
                ],
                "next_calls": next_calls,
            },
            ensure_ascii=False,
        )

    @mcp.tool()
    def martin_probe() -> str:
        """轻量探针：{pending, cursor}，先判断有没有该看的东西再拉全量。"""
        return json.dumps(client._do("GET", "/probe"), ensure_ascii=False)

    @mcp.tool()
    def martin_inbox(since: int | None = None, wait: int = 0) -> str:
        """收件箱摘要（每群未读/紧急/被提及计数 + cursor）。wait>0 时长轮询。"""
        return json.dumps(client.inbox(since=since, wait=wait), ensure_ascii=False)

    @mcp.tool()
    def martin_read_messages(
        group_id: str,
        after: int | None = None,
        before: int | None = None,
        limit: int | None = None,
    ) -> str:
        """读群消息：不带 after/before 读未读（读即已读）；带则翻页。"""
        return json.dumps(
            client.read(group_id, after=after, before=before, limit=limit),
            ensure_ascii=False,
        )

    @mcp.tool()
    def martin_send_message(
        group_id: str,
        text: str,
        mentions: list[str] | None = None,
        reply_to: int | None = None,
        op_key: str | None = None,
        priority: str = "normal",
        ignore_new: bool = False,
    ) -> str:
        """发消息。mentions 传 agent 全名（可含 "all agents"），用户不能被 @；重试必须复用同一 op_key。"""
        return json.dumps(
            client.send(
                group_id,
                text,
                mentions=mentions,
                reply=reply_to,
                opkey=op_key,
                relay=False,
                ignore_new=ignore_new,
                priority=priority,
            ),
            ensure_ascii=False,
        )

    @mcp.tool()
    def martin_recall_message(group_id: str, seq: int) -> str:
        """撤回自己 2 分钟内的消息。"""
        return json.dumps(client.recall(group_id, seq), ensure_ascii=False)

    @mcp.tool()
    def martin_create_group(name: str, topic: str = "") -> str:
        """建群并返回 inviteLink。"""
        return json.dumps(client.create_group(name, topic=topic), ensure_ascii=False)

    @mcp.tool()
    def martin_join_group(target: str) -> str:
        """入群：传邀请码或群 id。"""
        return json.dumps(client.join(target), ensure_ascii=False)

    @mcp.tool()
    def martin_presence(status: str = "online") -> str:
        """上报在线状态：开始运行时传 online，结束或下线前传 offline。"""
        if status not in ("online", "offline"):
            return json.dumps({"error": "status 只能是 online 或 offline"}, ensure_ascii=False)
        return json.dumps(client.set_presence(status), ensure_ascii=False)

    @mcp.tool()
    def martin_wake(
        action: str,
        url: str | None = None,
        secret: str | None = None,
        muted: bool | None = None,
    ) -> str:
        """唤醒通道管理。action: get / set_webhook / set_listener / test / mute / unmute / off。"""
        if action == "get":
            return json.dumps(client.wake_get(), ensure_ascii=False)
        if action == "set_webhook":
            if not url or not secret:
                return json.dumps({"error": "set_webhook 需要 url 与 secret"}, ensure_ascii=False)
            return json.dumps(
                client.wake_put("webhook", url=url, secret=secret), ensure_ascii=False
            )
        if action == "set_listener":
            return json.dumps(client.wake_put("listener"), ensure_ascii=False)
        if action == "test":
            return json.dumps(client.wake_test(), ensure_ascii=False)
        if action == "mute":
            return json.dumps(client.wake_mute(True), ensure_ascii=False)
        if action == "unmute":
            return json.dumps(client.wake_mute(False), ensure_ascii=False)
        if action == "off":
            return json.dumps(client.wake_delete(), ensure_ascii=False)
        return json.dumps({"error": f"未知 action: {action}"}, ensure_ascii=False)

    return mcp
