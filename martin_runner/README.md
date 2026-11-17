# martin-runner

把 Martin 的唤醒事件变成**一次 Claude Code 运行**，并把最终回复发回群里。

这是 DSH 推送桥的外部版：Claude Code 没有可注入的宿主插件，所以 runner 在外部own 进程、会话和工作目录。

```
Martin SSE ──→ martin-runner ──→ claude -p（一次性进程）
                    │                    │
                    │                    ├─ 首次 --session-id
                    │                    └─ 之后 --resume
                    ├─ SQLite: (group) → session_id / cwd / cursor
                    └─ POST /groups/{id}/messages（opKey 去重）
```

## 能力

- **工作空间固定**：`runner.workspace` 是唯一 cwd；每条会话记录同时存 `cwd`，不一致就开新会话，不跨目录 resume。
- **会话续接**：每个群一条 Claude session；首次 `--session-id`，之后 `--resume`。进程是一次性的，会话是持久的。
- **单飞**：一个 worker 顺序处理唤醒；同一群不会并发跑两个 Claude。
- **可靠性**：SSE `Last-Event-ID` 重连、listener 租约 + fence、失败批次留在 `pending` 表下次重试、游标成功后才推进。
- **回复**：取这一轮的最终 assistant 文本，用 `opKey=runner:<wakeId>` 发回群，重投不重复。
- **无人值守**：默认 `permission_mode=acceptEdits`、禁用 `AskUserQuestion`，不会停在交互提示。

## 谁控制什么

这一层刻意不替用户做决定：

| 控制点 | 谁决定 | 默认 |
|---|---|---|
| runner 进程是否启动 | 用户（前台 / launchd / 不启动） | 不启动；本包不会自安装服务 |
| 每次唤醒的 Claude 子进程 | runner 在收到事件时起，一次性 | 只有 runner 在跑才会发生 |
| 唤醒通道 | 用户 `martin wake --listener` | `auto_configure_wake = false`，runner 不改配置 |
| workspace 信任 | 用户显式 `martin-runner trust` | `trust_workspace = false` |
| 会话续接 | runner 只续自己创建的 session | 不碰交互会话 |
| DSH 推送桥 | 用户自行启用/停用 | 不要和 runner 用同一个成员 token 同时跑（唤醒消费者只能有一个，手册 §8.1 INV-10） |

`check` 只读；`run` 才连接 Hub 并取租约；`trust` 才会改 `~/.claude.json`（带备份）。

## 前置

- Python ≥ 3.11（Martin 的 venv 即可）
- 已安装并登录 Claude Code（`claude --version`）
- Martin 成员 token：`<workspace>/.martin/token`、`~/.martin/token` 或配置 `hub.token`
- **workspace 必须被 Claude Code 信任**。未信任时 Claude 不会持久化会话，`--resume` 会报 `No conversation found`。

```sh
# 方式一：在 workspace 里跑一次 claude，接受信任对话框
# 方式二：让 runner 写信任记录（会备份 ~/.claude.json）
python -m martin_runner.main trust --config runner.toml
```

## 快速开始

```sh
cp runner.example.toml runner.toml
# 编辑 workspace / base_url / token 来源

.venv/bin/python -m martin_runner.main check --config runner.toml
.venv/bin/python -m martin_runner.main run   --config runner.toml
```

`check` 会打印 workspace、Hub、token 前缀（不打印全文）、wake 通道、Claude 版本和信任状态。

前台跑通后，用 launchd/systemd 常驻。

## 配置

见 [`runner.example.toml`](../runner.example.toml)。关键项：

| 配置 | 默认 | 说明 |
|---|---|---|
| `hub.base_url` | `http://127.0.0.1:8000` | 可写根地址或 `/api/v1`，runner 会补全 |
| `hub.groups` | `[]` | 只处理这些群；空 = 全部 |
| `runner.workspace` | 必填 | Claude 的 cwd，也是会话隔离边界 |
| `runner.state_dir` | `~/.martin-runner` | SQLite、日志 |
| `claude.command` | `claude` | 绝对路径或 PATH 中的命令 |
| `claude.permission_mode` | `acceptEdits` | 无人值守必须显式给 |
| `claude.timeout_seconds` | `900` | 超时杀整个进程组 |
| `claude.trust_workspace` | `false` | true 时首次运行自动写 `~/.claude.json` |
| `claude.config_file` | `~/.claude.json` | 测试或自定义 `CLAUDE_CONFIG_DIR` 时用 |

## 状态

`runner.state_dir/state.db`：

- `sessions(group_id, session_id, cwd)`：每个群的会话与工作目录
- `cursors(group_id, seq)`：已成功处理的群游标
- `pending(group_id, messages, max_seq)`：失败未完成的批次，下次唤醒重试
- `runs(...)`：每次运行的审计（状态、耗时、cost）
- `kv(last_event_id)`：SSE 断线续传

忘掉某个群的会话：`python -m martin_runner.main forget --config runner.toml --group <id>`。

## launchd（macOS）

`~/Library/LaunchAgents/com.martin.runner.plist`（把示例里的 `/Users/you` 换成你的真实路径）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.martin.runner</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/IdeaProjects/Martin/.venv/bin/python</string>
    <string>-m</string><string>martin_runner.main</string>
    <string>run</string><string>--config</string>
    <string>/Users/you/IdeaProjects/Martin/runner.toml</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/you/IdeaProjects/Martin</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/you/.martin-runner/launchd.out.log</string>
  <key>StandardErrorPath</key><string>/Users/you/.martin-runner/launchd.err.log</string>
</dict>
</plist>
```

```sh
launchctl load ~/Library/LaunchAgents/com.martin.runner.plist
launchctl list | grep martin
```

## 限制

- 每次唤醒一个 Claude 进程：冷启动几秒，CPU/内存在运行期间才有。
- 只能续接 Claude Code **自己创建且已持久化**的会话；不会接管你在终端里正在用的会话。
- 只回传该轮最终文本；工具调用、推理、diff 不回传。
- 一次一个 worker；高频唤醒会排队，不会并发抢会话。
- 工作空间信任是 Claude Code 的状态，不是安全沙箱；需要强隔离请上容器/独立用户。
- 目前只实现 Claude；Codex 可按同样的 `thread/start` + `thread/resume` 协议扩展。
