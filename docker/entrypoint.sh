#!/bin/sh
set -eu

echo "[entrypoint] 等待 PostgreSQL 就绪..."
python - <<'PY'
import asyncio
import os
import sys

import asyncpg

dsn = os.environ["MARTIN_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)


async def wait_for_db() -> None:
    for attempt in range(1, 61):
        try:
            conn = await asyncpg.connect(dsn, timeout=5)
            await conn.close()
            print(f"[entrypoint] PostgreSQL 就绪（第 {attempt} 次尝试）", flush=True)
            return
        except Exception as exc:
            print(f"[entrypoint] 第 {attempt} 次连接失败：{exc}", flush=True)
            await asyncio.sleep(1)
    sys.exit("PostgreSQL 60 秒内未就绪")


asyncio.run(wait_for_db())
PY

echo "[entrypoint] 执行数据库迁移..."
alembic upgrade head

echo "[entrypoint] 启动 Hub..."
exec python -m martin.main
