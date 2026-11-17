# 开发说明

本文讲本地开发、跑测试和迁移约定。部署与运维见 [docs/deployment.md](docs/deployment.md)。

## 环境

- Python ≥ 3.11（验证用 3.13.15）
- PostgreSQL 17 或 18（验证用 18.6），本机 `localhost:5432` 可连，且运行账号有建库权限（测试会创建和删除 `groupchat_test`）
- Node ≥ 20（只用于前端构建，验证用 v24.22.1）

不需要 Redis：缓存后端只有进程内实现，设 `MARTIN_CACHE_BACKEND=redis` 会抛 `NotImplementedError`。

```sh
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"

cd web && npm ci && cd ..
```

## 本地跑起来

```sh
export MARTIN_DATABASE_URL="postgresql+asyncpg://<你>:<密码>@127.0.0.1:5432/groupchat"
export MARTIN_SECRET_KEY="$(openssl rand -hex 32)"
.venv/bin/python -m martin.main          # 默认 127.0.0.1:8000
```

首次启动建 schema 和缺失的表。前端改动用 Vite 开发服务器，它把 `/api` 代理到 8000：

```sh
cd web && npm run dev
```

改了后端没有热加载：uvicorn 未开 reload，要重启进程。只改前端产物不需要重启，刷新浏览器即可。若新界面依赖新端点，前端构建和后端要成对发布。

## 测试

```sh
.venv/bin/python -m pytest                       # 全套，当前 127 个用例
.venv/bin/python -m pytest tests/test_a_identity.py -q
```

必须用 `python -m pytest`。直接 `.venv/bin/pytest` 会因为测试里的 `from tests.conftest import ...` 报 `ModuleNotFoundError: No module named 'tests'`——脚本入口不会把仓库根放进 `sys.path`。

约束：

- 测试自己创建并删除 `groupchat_test` 库（会话级 fixture），不会碰你的开发库。因此运行账号需要建库权限。
- 全部用例使用假 agent（脚本或模拟客户端），不依赖真实 Claude Code / Codex。
- 用例按段落编号组织（A 接入身份、B 群、C 消息、N 紧急推送、P 管理员、Q P0 批次、R MCP、S 自助接入、T P1 增量……）。新增能力先补一条用例、写失败测试，再实现到通过。用例清单在内部维护，不随仓库发布。
- 限额类用例用可配置的小值（通过 `make_settings(**overrides)` 注入），不真实等待小时窗。

## 数据库迁移

结构变更一律写 Alembic 迁移，不要依赖启动建表：`create_schema_and_tables` 走 `Base.metadata.create_all`，只补建缺失的表，**不会给已存在的表加列**。代码比库新时进程能起、首页正常，整实体查询会因缺列 500。

```sh
.venv/bin/alembic current                        # 库现在在哪一版
.venv/bin/alembic heads                          # 代码要求的最新版
.venv/bin/alembic revision -m "加某某列"          # 手写迁移，别用 autogenerate 直接提交
.venv/bin/alembic upgrade head
```

约定：

- 版本号沿用四位数字前缀（`0001_initial.py` … `0010_owner_session_version.py`），文件名带简短英文说明。
- `alembic/env.py` 在线迁移结尾显式 `await connection.commit()`；去掉这行会导致迁移回滚。
- 迁移读取与 Hub 同一套 `MARTIN_` 环境变量。
- 对已有数据跑迁移前先备份结构：`pg_dump -s <库名> > schema-before.sql`。
- 只读列、只加不改语义；历史数据回填写在同一迁移里（做法见 `0008` 迁移：给存量成员与群回填唯一码）。

## 代码约定

- 路由体内显式取身份：`member = await current_member(request, db)`，配合 `db: AsyncSession = Depends(get_db)`。不要把鉴权依赖再嵌套进 `Depends`——FastAPI 0.162 对嵌套 `Depends` 返回模型会报 `Invalid args for response field`。
- 错误统一抛 `AppError(code, message, status)`，由 `main.py` 的异常处理器包成 `{error: {code, message}, schema}`；端点不要自己拼错误 JSON。
- 对外标识用带前缀的唯一码（agent `a…`、人类成员 `u…`、群 `g…`），内部整数主键与消息 `seq` 不暴露。
- 配置项加在 `martin/config.py` 的 `Settings` 上，用 `MARTIN_` 前缀环境变量覆盖；限额不要写死在业务代码里。
- 用户可见文案（Web、CLI 输出、Agent 手册）写具体可操作的陈述，说明"是什么、怎么做"。
- 注释默认不写；只在写清一个非显而易见的约束时加一行。

## Agent 手册版本

- 手册正文的版本号在 `web/src/agent-manual.md` §0（`版本：vN`），Web 页面直接读取这个值，不要在别处再写死。
- 发布新版本时，同步更新 `web/src/manual-versions.ts` 的 `MANUAL_VERSIONS`（版本说明）和 `MANUAL_UPGRADES`（实际提供的增量补丁）。只有登记了 `from → to` 的旧版本才能在页面上选择增量升级；没有登记的组合只能发完整手册。

## 提交前

1. `.venv/bin/python -m pytest` 全绿。
2. 改了前端：`cd web && npm run build`（`tsc -b` 会做类型检查）。
3. 改了结构：确认 `alembic upgrade head` 在开发库上跑通，并同步更新 `docs/deployment.md` 的升级说明。
4. 改了能力边界：同步 README 的"当前状态"，并在 `docs/iteration-log.md` 追加一条迭代记录。
