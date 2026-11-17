#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
    echo "未找到 docker，请先安装 Docker Engine 与 Compose v2" >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "未找到 docker compose（需要 v2）" >&2
    exit 1
fi

if [ ! -f .env ] && ! command -v openssl >/dev/null 2>&1; then
    echo "需要 openssl 生成随机密码与密钥（apt install openssl）" >&2
    exit 1
fi

if [ ! -f .env ]; then
    umask 077
    cat > .env <<EOF
MARTIN_BIND=0.0.0.0
MARTIN_PUBLISH_PORT=8000

POSTGRES_USER=martin
POSTGRES_PASSWORD=$(openssl rand -hex 16)
POSTGRES_DB=martin

MARTIN_SECRET_KEY=$(openssl rand -hex 32)
EOF
    echo "已生成 .env（随机数据库密码与会话密钥，权限 600），请备份保存"
fi

. ./.env

# Alembic 只允许每个 revision 出现一次；服务器上残留的旧迁移文件会让
# `alembic upgrade head` 报 Multiple head revisions，构建前先给出明确提示。
duplicates=$(grep -h '^revision' alembic/versions/*.py 2>/dev/null | sed 's/[^0-9A-Za-z_]//g' | sort | uniq -d || true)
if [ -n "$duplicates" ]; then
    echo "错误：alembic/versions 中存在重复的 revision：$duplicates" >&2
    echo "请只保留当前仓库的迁移文件（0001–0011），删除服务器上残留的旧文件后重试。" >&2
    ls -1 alembic/versions/*.py >&2
    exit 1
fi

docker compose up -d --build

printf '等待 Hub 就绪'
i=0
while ! docker compose exec -T hub python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1', timeout=2)" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
        echo
        echo "Hub 60 秒内未就绪，查看日志：docker compose logs hub" >&2
        exit 1
    fi
    printf '.'
    sleep 1
done
echo " 就绪"

echo
echo "前端产物："
docker compose exec -T hub sh -c 'ls -1 web/dist/assets' | sed 's/^/  /'
echo "镜像：$(docker compose images hub 2>/dev/null | tail -n +2 || true)"

cat <<EOF

Martin Hub 已启动：http://<服务器IP>:${MARTIN_PUBLISH_PORT}

接下来（只需一次）：
1. 浏览器打开上面的地址，创建首个用户账号（入口只在空库出现，请先完成以免被他人抢占）
2. 创建管理员账号：
   docker compose exec hub martin admin create-admin --username admin --password '<强密码>'
3. 其他用户从登录页注册，由管理员在 /admin 审批

常用命令：
  查看日志：docker compose logs -f hub
  备份数据库：docker compose exec -T db pg_dump -U ${POSTGRES_USER} -Fc ${POSTGRES_DB} > martin-\$(date +%F).dump
  升级：同步代码后再次执行 ./deploy.sh（自动重建镜像并迁移）
EOF

if [ "${MARTIN_BIND}" = "0.0.0.0" ]; then
    echo
    echo "注意：8000 端口当前直接对外。正式使用请前置 Nginx/Caddy 终止 TLS，并把 MARTIN_BIND 改为 127.0.0.1。"
fi
