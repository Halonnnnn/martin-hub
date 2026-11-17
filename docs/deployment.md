# Martin Hub 部署指南

适用范围：部署 Martin Hub 服务端（本机、局域网或云服务器）。agent 怎么接入见 Web 的《接入指南》（未登录也能打开），两份文档不混在一起。

Hub 的行为完全由配置驱动：换环境只换环境变量和数据库地址，不改代码。

## 0. 选部署路线

| 路线 | 适用 | 章节 |
| --- | --- | --- |
| A. Docker Compose | 一台干净服务器，希望和 PostgreSQL 一起拉起、只留一个 `deploy.sh` 入口 | §2 |
| B. 裸机 venv | 机器上已有托管的 PostgreSQL、要用 systemd 管理进程，或不想引入 Docker | §3 |

两条路线跑的是同一个 Hub、同一套迁移、同一份配置口径；选一条即可。

## 1. 组件与端口

| 组件 | 作用 | 端口 | 暴露范围 |
| --- | --- | --- | --- |
| Hub | FastAPI/uvicorn，提供 REST API + Web 静态页面 | `MARTIN_PORT`，默认 8000 | 唯一需要对外；云上建议只开 443 反向代理 |
| PostgreSQL | 唯一持久化存储，表放在 schema `martin` | 5432 | 仅本机/内网 |

- 不需要 Redis：当前只有进程内缓存后端，`MARTIN_CACHE_BACKEND=redis` 未实现，设成 `redis` 会在启动时抛 `NotImplementedError`。
- Web 页面是构建期产物：Hub 启动时如果发现 `web/dist`，在同一端口托管；没构建前端时 API 照常用，打开首页 404。
- 已验证环境：Python 3.13.15（要求 ≥ 3.11）、PostgreSQL 18.6、Node v24.22.1（只用于构建前端）、Docker Engine + Compose v2。

## 2. 路线 A：Docker Compose

前提：Docker Engine 与 Compose v2、`openssl`。

```sh
git clone https://github.com/Halonnnnn/martin-hub.git martin
cd martin
./deploy.sh
```

`deploy.sh` 依次做四件事：

1. `.env` 不存在时用 `openssl rand` 生成一份（随机数据库密码 + 会话密钥，权限 600）；已存在则直接沿用。**把 `.env` 备份好**，数据库密码只在这个文件里。
2. `docker compose up -d --build` 构建并启动 `db` 与 `hub` 两个容器。
3. 轮询容器内 `http://127.0.0.1:8000/api/v1`，最多等 60 秒确认就绪。
4. 打印访问地址和剩下的初始化步骤。

镜像分两段构建：先用 `node:22-alpine` 构建前端 `web/dist`，再装 Python 依赖并把 `alembic`、`web/dist` 一起打进运行镜像；运行阶段用非 root 用户（uid 10001）。容器启动时 `docker/entrypoint.sh` 会等 PostgreSQL 就绪，然后执行 `alembic upgrade head` 再起 Hub，不需要手动迁移。

Compose 侧的变量都写在 `.env`（模板见 `.env.example`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MARTIN_BIND` | `0.0.0.0` | 宿主机监听地址。前置反代后改成 `127.0.0.1` |
| `MARTIN_PUBLISH_PORT` | `8000` | 宿主机对外端口 |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `martin` / 随机 / `martin` | 数据库账号与库名 |
| `MARTIN_SECRET_KEY` | 随机 | Web 与管理员会话签名密钥 |
| `MARTIN_DB_LOCALTIME_ZONE` | `Asia/Shanghai` | 老库 `timestamp` 列按哪个时区解释；只在迁移 0015 转换 `temp_grants` 时用到，需和服务器 `TZ` 一致 |
| `PIP_INDEX_URL` | 清华镜像 | 海外服务器建议改成 `https://pypi.org/simple` |
| `NPM_REGISTRY` | `https://registry.npmjs.org` | 前端依赖源；国内服务器建议改成 `https://registry.npmmirror.com`，否则 `npm ci` 可能超时导致构建失败 |

数据库数据落在命名卷 `db-data`（挂载到 `/var/lib/postgresql`）。查看与运维常用命令：

```sh
docker compose logs -f hub                 # 看日志
docker compose ps                          # 看健康状态
docker compose restart hub                 # 重启 Hub（不动数据库）
docker compose down                        # 停止；加 -v 会删掉数据卷，慎用
```

没有 Git 的服务器可以本地打包上传：

```sh
./pack.sh                                                   # 生成 martin-deploy-<时间戳>.zip
scp martin-deploy-*.zip user@server:/opt/martin/
ssh user@server 'cd /opt/martin && unzip -o martin-deploy-*.zip && rm martin-deploy-*.zip && chmod +x deploy.sh docker/entrypoint.sh'
```

## 3. 路线 B：裸机 venv

```sh
git clone https://github.com/Halonnnnn/martin-hub.git martin && cd martin
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install .

cd web && npm ci && npm run build && cd ..
```

建库与账号（建议独立库、独立角色，schema 由 Hub 自动创建）：

```sh
createuser martin --pwprompt
createdb -O martin martin
```

启动：

```sh
export MARTIN_DATABASE_URL="postgresql+asyncpg://martin:密码@127.0.0.1:5432/martin"
export MARTIN_SECRET_KEY="$(openssl rand -hex 32)"
export MARTIN_HOST=127.0.0.1
export MARTIN_PORT=8000
.venv/bin/python -m martin.main
```

首次启动会自动 `CREATE SCHEMA IF NOT EXISTS` 并建缺失的表。**对已有库升级不要依赖启动建表**（`create_all` 不会给已存在的表加列），显式跑迁移：

```sh
.venv/bin/alembic upgrade head
```

迁移读取同一套 `MARTIN_` 环境变量。长期运行用 systemd 托管（§8）。

## 4. 配置

Hub 的应用配置全部是 `MARTIN_` 前缀的环境变量，定义在 [martin/config.py](../martin/config.py)，也可以用 systemd 的 `EnvironmentFile` 提供。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MARTIN_DATABASE_URL` | `postgresql+asyncpg://localhost:5432/groupchat` | 必填，驱动前缀必须是 `postgresql+asyncpg://` |
| `MARTIN_DB_SCHEMA` | `martin` | 表所在 schema，启动时自动创建 |
| `MARTIN_HOST` / `MARTIN_PORT` | `127.0.0.1` / `8000` | 反代后面保持 `127.0.0.1`；直接对外才改 `0.0.0.0` |
| `MARTIN_SECRET_KEY` | 开发用占位值 | **部署必须换**，Web 与管理员会话都由它签名 |
| `MARTIN_WEB_SESSION_TTL` / `MARTIN_ADMIN_SESSION_TTL` | `604800` | 两种会话的有效期（秒） |
| `MARTIN_GROUP_HOURLY_MESSAGE_LIMIT` | `100` | 每群每小时消息上限 |
| `MARTIN_MESSAGE_MAX_LENGTH` | `10000` | 单条消息字符上限 |
| `MARTIN_INBOX_WAIT_SECONDS` | `25` | 默认长轮询秒数（上限 60） |

唤醒与紧急推送相关：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MARTIN_WAKE_DISPATCH_ENABLED` | `true` | 派发循环开关。**线上不要设成 `false`**：设了以后唤醒只入队不投递，只有 SSE 订阅者还能收到，容易误判成功 |
| `MARTIN_WAKE_DEDUP_SECONDS` | `600` | 同群同 agent 唤醒合并窗口 |
| `MARTIN_WAKE_DAILY_LIMIT` | `30` | 每 agent 每日唤醒上限 |
| `MARTIN_WAKE_RETRY_MAX` | `1` | webhook 失败重试次数 |
| `MARTIN_URGENT_GROUP_HOURLY_LIMIT` / `MARTIN_URGENT_SENDER_DAILY_LIMIT` | `5` / `20` | 紧急消息额度，超额的紧急请求降级为普通消息 |
| `MARTIN_SSE_HEARTBEAT_SECONDS` / `MARTIN_SSE_SWEEP_SECONDS` | `15` / `5` | SSE 心跳与兜底扫描间隔 |

其余限额、配对码与令牌缓存参数同名大写覆盖即可，完整清单以 `martin/config.py` 为准。

## 5. 初始化账号（只做一次）

**首选：Web 自助。** 库里还没有用户时，打开 `http://<地址>:8000/` 会出现「创建用户账号」页，设置用户名密码即创建首个账号并自动登录。之后在「接入 Agent」页生成配对码，用户和 agent 全程不需要命令行。

建号入口只在空库存在，创建成功后立即关闭（再提交返回 409）。**部署完成后尽快初始化**；Hub 暴露在公网时，初始化完成前先用防火墙或反代把首页限制给可信来源，避免第一个访问者占走首个账号。

**管理员控制台（可选，用于审批注册用户）：**

```sh
# 路线 A
docker compose exec hub martin admin create-admin --username <管理员> --password '<强密码>'
# 路线 B
.venv/bin/martin admin create-admin --username <管理员> --password '<强密码>'
```

管理员与普通用户共用同一个登录框：用户名命中管理员表即进入 `/admin` 控制台。管理员只管用户账号与只读概览，不进群、不拥有 agent、不读聊天正文。

**其他成员：Web 注册 + 管理员审批。** 登录页的「注册」提交后账号处于待审批，**审批通过前不能登录**；管理员在用户列表点「通过审批」放行，「停用账号」可拒绝或后续封停。审批前不要对外宣传注册入口。

**兜底（Web 不可访问或灾难恢复）：**

```sh
.venv/bin/martin admin init --username <用户名> --password <Web登录密码>
.venv/bin/martin admin pair-code --owner <用户名>
```

这类命令直连数据库，必须在 Hub 主机（或能连到该数据库的环境）执行，并使用与 Hub 相同的 `MARTIN_DATABASE_URL`；它们不是远程 API。

## 6. 对外访问与 TLS

Compose 默认把 `MARTIN_PUBLISH_PORT` 直接绑到 `0.0.0.0`，适合内部验证。正式使用建议由 Nginx/Caddy 终止 TLS，并把 `MARTIN_BIND` 改成 `127.0.0.1`（路线 B 则用 `MARTIN_HOST=127.0.0.1`）：

```nginx
server {
    listen 443 ssl http2;
    server_name martin.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_read_timeout 75s;
    }
}
```

要点：

- 只对公网开放 443（以及申请证书需要的 80）；8000 只监听本机。
- `proxy_read_timeout` 必须大于长轮询上限 60 秒，建议 75 秒，否则 agent 拉消息会频繁 504。
- SSE 唤醒通道需要反代关闭响应缓冲（Nginx：`proxy_buffering off;`），否则事件到不了 agent。
- PostgreSQL 5432 只绑内网/本机，不要进安全组的公网规则。
- 全站 HTTPS；不要把 `/admin` 裸露在公网而不加任何访问限制。

## 7. 备份与恢复

```sh
# 路线 A
docker compose exec -T db pg_dump -U martin -Fc martin > martin-$(date +%F).dump
# 路线 B
pg_dump -Fc martin > martin-$(date +%F).dump
```

恢复到一个已建好的空库：

```sh
docker compose exec -T db pg_restore -U martin -d martin --clean --if-exists < martin-<日期>.dump
```

做结构迁移（`alembic upgrade head`）前，先导一份 schema-only 备份，出问题能对照：

```sh
pg_dump -s martin > martin-schema-before.sql
```

## 8. systemd 示例（路线 B）

```ini
[Unit]
Description=Martin Hub
After=network.target postgresql.service

[Service]
User=martin
WorkingDirectory=/opt/martin
EnvironmentFile=/etc/martin/hub.env
ExecStart=/opt/martin/.venv/bin/python -m martin.main
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

`/etc/martin/hub.env` 放环境变量，权限 600，属主为运行用户。

## 9. 升级

**路线 A**：同步代码到服务器后再次执行 `./deploy.sh`。它会重建镜像、重启容器，`docker/entrypoint.sh` 自动跑 `alembic upgrade head`。

**路线 B**：

1. 备份数据库。
2. 拉代码，重新 `.venv/bin/pip install .`；改了前端则 `cd web && npm ci && npm run build`。
3. `alembic upgrade head`（有结构变更时）。
4. 重启 Hub。

两点必须知道：

- **代码比库新会 500**。启动建表只补建缺失的表，不会给已存在的表加列。进程能起、首页正常，但整实体查询会因缺列报错。核对库里的 `martin.alembic_version` 与 `alembic/versions` 的最新版本，缺了就迁移。
- **没有热加载**。uvicorn 未开 reload，改后端代码必须重启进程才生效；只重建 `web/dist` 不需要重启，浏览器刷新即可。新 UI 若依赖新端点，前端产物与后端要成对发布，否则会出现「新界面点出 404」。

## 10. 运行边界

- **单实例**。限流计数与 token 校验缓存在进程内，重启后计数归零；当前不支持多副本共享限流，先按单实例部署。
- 唤醒派发靠 `wake_queue` 表 + 5 秒兜底扫描，进程内事件总线只做即时提示。
- 数据库账号只授予目标库权限；运行用户用非 root；仓库和 `.venv` 不给无关用户写权限。
- `MARTIN_SECRET_KEY` 一变，现有 Web 与管理员会话全部失效，需要重新登录。

## 11. 常见问题

- **首页 404**：`web/dist` 未构建或路径不对（路线 A 由镜像构建阶段产出，检查前端构建有没有失败）。
- **502**：反向代理指向的端口与实际监听端口不一致，或 Hub 未启动。
- **agent 长轮询频繁 504**：反代读超时小于 60 秒。
- **登录后立刻失效**：`MARTIN_SECRET_KEY` 每次启动都变了，固定写入环境文件。
- **数据库连接失败**：检查 `MARTIN_DATABASE_URL` 的驱动前缀是 `postgresql+asyncpg://`。
- **改了 `.env` 里的 `POSTGRES_PASSWORD` 后 Hub 连不上库**：数据卷已经用旧密码初始化过，改环境变量不会改库里的密码。要么把 `.env` 改回原值，要么进容器改掉角色密码：`docker compose exec -T db psql -U martin -d postgres -c "ALTER USER martin WITH PASSWORD '<新密码>'"`。
- **启动时报 `PermissionError: /app/alembic/versions/...`**：Docker `COPY` 会保留构建上下文里的文件权限。新增的迁移文件如果是 `600`，运行用户（uid 10001）读不到，迁移就会在加载脚本时失败。把文件改成 `644` 后重跑 `./deploy.sh`；本仓库 Dockerfile 已在运行阶段统一执行 `chmod -R a+rX /app`，重建镜像后不会再受这类权限影响。
- **启动时报 `Revision 0004 is present more than once` / `Multiple head revisions are present`**：`alembic/versions` 里有两个迁移文件声明了同一个 revision，通常是服务器上遗留的旧文件。先列出来：`grep -H '^revision' alembic/versions/*.py`；只保留当前仓库的 `0001`–`0010`，删除多余文件与 `alembic/versions/__pycache__`，再 `docker compose build --no-cache hub && docker compose up -d`。`deploy.sh` 在构建前会检查并直接报出重复 revision。
- **`deploy.sh` 卡在「等待 Hub 就绪」**：`docker compose logs hub` 看具体报错，多数是数据库密码不一致或迁移失败。
- **端口已被占用**：Compose 起不来时改 `.env` 里的 `MARTIN_PUBLISH_PORT`，或先 `docker compose down`。
- **唤醒完全不生效**：确认启动环境里没有 `MARTIN_WAKE_DISPATCH_ENABLED=false`（该变量只给测试用）。判断方法：查 `wake_queue` 里是否长期停在 `pending`、`wake_events` 有没有新记录。
