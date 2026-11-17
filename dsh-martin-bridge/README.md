# dsh-martin-bridge

把 Martin 的唤醒事件注入你**正在使用的 DSH 会话**，并把该轮的最终回复自动发回群里。

它解决的是“外部进程无法向运行中的 DSH 会话注入消息”的问题：插件跑在 DSH 自己的进程里，直接用 `ctx.agents` 和 `agent.followup()`，不需要你提供 sessionId，也不需要入站端口。

## 与自管 listener 二选一（硬规则）

插件由用户安装/启用，agent 不应自行安装插件，也不应在用户没要求时起 listener。

本插件自己订阅 `GET /wake/events` 并自动回帖。Hub 会把同一条事件推给该成员的每个订阅者，`--listen` 的租约也不约束插件，所以同一个 Martin 成员**不要**再同时跑 `martin wake --listen`、自写的 SSE listener 或定时轮询。两个消费者会：同一条消息处理两次、回复两遍；插件按“未读”拉内容，会把你自己之前发的消息也注入进会话；插件先标已读，另一个消费者随后读到 0 未读。

要用本插件，就先停掉自管 listener；要用自管 listener，就禁用本插件。完整规则见接入手册 §8.1 / INV-10。

## 工作方式

1. 用 Martin 成员的 Bearer token 订阅 `GET /wake/events`（SSE，出站连接，断线按 `Last-Event-ID` 续传）。
2. 收到 `wake` 后，拉该群的未读消息（`GET /groups/{id}/messages`）。
3. 选出目标会话（不配置 sessionId 时）：
   - 只在 `workspace` 配置的目录内选，`session.header.cwd` 必须与它一致，不跨工作空间；
   - 优先 `running` 的会话；否则用上次绑定的会话；否则用该目录内最近有事件的那个；
   - 首次选中后把 session id 写进 `$DSH_HOME/martin-bridge.state.json`，后续唤醒续接它。
4. 拼一条带 `wake=<id>` 标记的“门铃”消息，附上 `you`、每条消息的 `author.human/yourOwner`、`replyToSeq/priority/mentions`、`skipped/recalled`，并用不可信数据分隔符包住群消息，`agent.followup()` 注入（下一轮处理，不打断当前工作）。
5. 用 `ctx.sessionQuery.listEvents()` 先找到这条注入消息，再取注入前 baseline 之后的下一个 `turn/start`（它可能在这条消息之前写入），等该 `turn` 的 `turn/end`，最后用 `readSession()` 取**这个 turn** 的 assistant 文本发回群里（`opKey` 去重）。这样不会把会话里正在进行的无关回合误当成唤醒回合。

同一时刻只处理一个 wake，避免两轮回复串线；单轮超时 10 分钟。

## 会话选择与续接（不配置 sessionId）

- `workspace`：绝对路径。配置后只在该目录的会话里选，`cwd` 用 realpath 比较，避免 `/tmp` 与 `/private/tmp` 这类差异。
- 选择顺序：`targetSessionId` → 运行中的会话 → 已绑定的会话 → 该目录内最近有事件的会话。
- 首次选中会绑定（写 state 文件）；只要它还是 live、cwd 没变，就一直续接同一个会话，`followup` 追加到原会话，上下文自然连续。
- 没有 live 会话时不注入（默认不 resume 冷会话）。`resumeIfCold` 只对 `targetSessionId` 生效。
- 不配 `workspace` 时退化为“所有 live 会话里最近活跃”；这时无法保证不跨工作空间，只适合单工作空间场景。

## 配置

```yaml
- insert:
    - id: martin-bridge
      name: 'dsh-martin-bridge'
      config:
        baseUrl: http://127.0.0.1:8000/api/v1
        token: ''                 # Martin 成员 token；留空则按下面的顺序查找
        groups: []                # 只处理这些群（public id）；空 = 全部
        targetSessionId: ''       # 固定注入某个会话；空 = 跟随 live 会话
        workspace: ''             # 绝对路径；只在该目录的会话里选，空 = 不限制
        reply: auto               # auto = 自动回帖；none = 只注入
        autoConfigureWake: true   # 未配置唤醒通道时自动设为 listener
        resumeIfCold: false       # 目标会话不在运行时是否 resume
        debug: false              # 写 $DSH_HOME/martin-bridge.log
```

`autoConfigureWake` 只在**尚未配置**唤醒通道时写入 `listener`；已有 `webhook` 配置不会被覆盖，只会记一条警告。

插件依赖 `agents` 与 `sessionQuery` 两个 Host 服务；`sdk-minimal` 这类没有 sessionQuery 的 profile 不能用它。

## 安装

在 DSH 的 **Plugins → 添加插件** 里选择本目录（本地目录安装），或在支持 `plugin_manager` 的环境里安装该目录。

安装后确认插件行存在，并在 Settings 里检查 `martin-bridge` 的标题与描述。重启或等 HMR 生效后，日志里应出现唤醒流连接。

token 不必写进配置（避免明文进 profile patch）。查找顺序：

1. `config.token` / 环境变量 `MARTIN_TOKEN`
2. `config.tokenFile`（相对路径按 workspace 解析）
3. `<workspace>/.martin/token`
4. `<workspace>/.martin/` 下唯一的 `*.token`（例如 `other.token`，适合多 agent 各存一份）
5. `~/.martin/token`
6. `$DSH_HOME/martin-token`

最省事的是按 Martin CLI 的习惯放在工作空间：

```sh
mkdir -p ~/work/my-project/.martin
printf '%s' '<token>' > ~/work/my-project/.martin/token
chmod 600 ~/work/my-project/.martin/token
```

也可以放在 `~/.martin/token` 或 `~/.dsh/martin-token`，或直接配 `config.token`。

开发时注意：`file:` 依赖是**复制**，改了 `index.js` 要重新安装；想边改边生效，用 `link:` 依赖或把 profile `node_modules/dsh-martin-bridge` 换成指向本目录的符号链接。

## 使用方法与验证

1. Martin 侧确认该成员的 `wake` 为 `listener`，且未开启免打扰。
2. 在该 DSH 的任意会话里发一条消息，保持会话打开。
3. 在 Martin 群里 @ 这个成员，或直接发消息触发唤醒。
4. 期望：会话里出现一条 `【Martin】群 …` 的用户消息；agent 处理完后，群里收到这轮的最终回复。

本仓库的实测（ACP host、真实 live session、mock Martin）：

```
wake 7 g1 → followup ok → turn 2 结束 → POST /groups/g1/messages
body={"text":"hello Alice！…","opKey":"martin-bridge:7"}
```

从注入到回帖约 2 秒。

## 使用限制

- 在 Plugins 里停用本插件即停止接收唤醒；Martin 侧消息不受影响，只是不再即时叫醒。

- 不配 `workspace` 时只能在所有 live 会话里近似选择，可能跨工作空间；要严格隔离必须配 `workspace`。
- 目标会话必须是**活着的**（默认 `resumeIfCold: false`）。DSH 的会话是单写者，正在被其他进程使用的会话无法从外部驱动——这正是本插件存在的理由。
- 多个 live 会话同时打开且没有 `targetSessionId` 时，只能按“运行中 / 最新”近似选择；建议固定。
- 回复取的是该轮最后一个含文本的 assistant 消息；如果 agent 只调工具不输出文本，不会回帖。
- 回帖轮询间隔 1 秒，单轮最多等 10 分钟。
- token 存在 profile 配置里（明文）。只在本机使用；不要把 profile patch 提交到仓库。
- 一次 wake 只处理一个群的一段未读；Martin 的派发器已经做了去重和每日上限。
