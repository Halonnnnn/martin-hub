# 端到端验证记录

环境：DSH 0.2.2-rc.2，`acp` profile，真实 live session，mock Martin（SSE + REST）。

## 步骤

1. 把本包安装进 `acp` profile（本地目录），配置 `baseUrl=http://127.0.0.1:7799`、`token=test`、`debug=true`。
2. 启动 ACP host，创建 session `575ca6d2-…`，先发一轮 prompt。
3. mock Martin 通过 SSE 发 `wake`（`id=7`, `groupId=g1`, `reason=mention`）。
4. 插件拉取未读消息，`followup()` 注入 live session。
5. agent 完成第二轮；插件轮询 `listEvents` 看到新的 `turn/end`，用 `readSession` 取最后 assistant 文本。
6. 插件把文本 POST 回 mock Martin。

## 观测

```
activated {... "listEvents":"function"}
sse connected
wake 7 g1
deliver 7 session= 575ca6d2-…
messages 1
followup ok 575ca6d2-… beforeSeq= 16
replied g1 7
```

mock Martin：

```
GET /wake/events
GET /groups/g1/messages?limit=50
POST /groups/g1/messages body={"text":"hello Alice！欢迎～有什么我可以帮你的吗？","opKey":"martin-bridge:7"}
```

session 日志确认第二轮：

```
turn/start 2
user/message 【Martin】群 g1 有新消息…
assistant/message 2
turn/end 2
```

从注入到回帖约 2 秒。

## 已知边界

- `ctx.sessionQuery` 必须在 `inject` 中声明，否则 Host 报 `cannot get property "sessionQuery" without inject`。
- `readSurface()` 只含消息，不含 `turn/start`/`turn/end`；轮次检测必须用 `listEvents()`。
- `file:` 安装是复制，改代码后要重装或用 `link:`。
