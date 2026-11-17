#!/usr/bin/env sh
# 服务器迁移：旧服务器导出数据库与配置，新服务器恢复并启动 Hub。
# 代码本身用 rsync/git 同步，不打包在迁移包里。
set -eu
cd "$(dirname "$0")"

usage() {
    cat <<'EOF'
用法：
  ./migrate.sh export
      旧服务器执行：导出数据库与 .env，生成 martin-migrate-<时间>.tar.gz
  ./migrate.sh import <包.tar.gz> [--force]
      新服务器执行：恢复数据并启动 Hub；目标已有数据时需加 --force 覆盖

迁移包内含数据库与密钥，注意妥善保管，不要提交到仓库。
EOF
}

need_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "未找到 docker，请先安装 Docker Engine 与 Compose v2" >&2
        exit 1
    fi
    if ! docker compose version >/dev/null 2>&1; then
        echo "未找到 docker compose（需要 v2）" >&2
        exit 1
    fi
}

wait_db() {
    printf '等待数据库就绪'
    i=0
    while ! docker compose exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
        i=$((i + 1))
        if [ "$i" -ge 60 ]; then
            echo
            echo "数据库 60 秒内未就绪，查看日志：docker compose logs db" >&2
            exit 1
        fi
        printf '.'
        sleep 1
    done
    echo " 就绪"
}

wait_hub() {
    printf '等待 Hub 就绪'
    i=0
    while ! docker compose exec -T hub python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1', timeout=2)" >/dev/null 2>&1; do
        i=$((i + 1))
        if [ "$i" -ge 90 ]; then
            echo
            echo "Hub 90 秒内未就绪，查看日志：docker compose logs hub" >&2
            exit 1
        fi
        printf '.'
        sleep 1
    done
    echo " 就绪"
}

psql_q() {
    docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1" 2>/dev/null | tr -d '[:space:]'
}

load_env() {
    # shellcheck disable=SC1091
    . ./.env
    POSTGRES_USER=${POSTGRES_USER:-martin}
    POSTGRES_DB=${POSTGRES_DB:-martin}
}

cmd_export() {
    need_docker
    if [ ! -f .env ]; then
        echo "未找到 .env：请先在当前（旧）服务器执行 ./deploy.sh 完成部署后再导出" >&2
        exit 1
    fi
    load_env

    if [ -z "$(docker compose ps -q db 2>/dev/null || true)" ]; then
        echo "db 容器未运行：请先启动（./deploy.sh 或 docker compose up -d）" >&2
        exit 1
    fi
    wait_db

    ts=$(date +%Y%m%d-%H%M%S)
    pkg="martin-migrate-$ts.tar.gz"
    stage=$(mktemp -d)
    trap 'rm -rf "$stage"' EXIT
    chmod 700 "$stage"

    echo "导出数据库（在线一致性快照，无需停机）..."
    docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$stage/db.dump"
    cp .env "$stage/env"
    chmod 600 "$stage/db.dump" "$stage/env"

    ver=$(psql_q "select version_num from martin.alembic_version" || true)
    owners=$(psql_q "select count(*) from martin.owners" || true)
    msgs=$(psql_q "select count(*) from martin.messages" || true)
    cat > "$stage/meta.txt" <<META
导出时间: $(date '+%F %T')
主机: $(hostname)
alembic: ${ver:-unknown}
owners: ${owners:-0}
messages: ${msgs:-0}
META

    tar czf "$pkg" -C "$stage" ./db.dump ./env ./meta.txt
    chmod 600 "$pkg"

    echo
    echo "已生成迁移包：$pkg （内含数据库与密钥，注意保管）"
    tar -xOf "$pkg" ./meta.txt | sed 's/^/  /'
    echo
    echo "下一步：把代码同步到新服务器后，在新服务器执行："
    echo "  ./migrate.sh import $pkg"
}

cmd_import() {
    pkg=${1:-}
    force=${2:-}
    if [ -z "$pkg" ] || [ ! -f "$pkg" ]; then
        echo "请指定迁移包：./migrate.sh import <包.tar.gz> [--force]" >&2
        exit 1
    fi
    need_docker

    if [ ! -f .env ]; then
        tar -xOf "$pkg" ./env > .env
        chmod 600 .env
        echo "已从迁移包恢复 .env（沿用源服务器的数据库密码与会话密钥）"
    else
        src_secret=$(tar -xOf "$pkg" ./env | sed -n 's/^MARTIN_SECRET_KEY=//p')
        # shellcheck disable=SC1091
        . ./.env
        if [ -n "$src_secret" ] && [ "$src_secret" != "${MARTIN_SECRET_KEY:-}" ]; then
            echo "提示：当前 .env 的 MARTIN_SECRET_KEY 与迁移包不同，已有浏览器会话将失效（需重新登录）。"
        fi
    fi
    load_env

    echo "构建镜像并启动数据库与 Hub（首次会执行迁移）..."
    docker compose up -d --build
    wait_db
    wait_hub

    owners=$(psql_q "select count(*) from martin.owners" || true)
    if [ "${owners:-0}" != "0" ] && [ "$force" != "--force" ]; then
        echo "目标库已有 ${owners} 个用户。确定要覆盖现有数据请加 --force：" >&2
        echo "  ./migrate.sh import $pkg --force" >&2
        exit 1
    fi

    echo "停止 Hub，准备恢复数据..."
    docker compose stop hub >/dev/null 2>&1 || true

    echo "清空目标 schema martin..."
    docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c 'DROP SCHEMA IF EXISTS martin CASCADE' >/dev/null

    echo "恢复数据..."
    tar -xOf "$pkg" ./db.dump | docker compose exec -T db pg_restore --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"
    echo "数据已恢复"

    echo "启动 Hub（自动把库升级到当前代码的迁移版本）..."
    docker compose up -d hub
    wait_hub

    echo
    echo "迁移完成，核对如下："
    tar -xOf "$pkg" ./meta.txt | sed 's/^/  导出时  /'
    ver=$(psql_q "select version_num from martin.alembic_version" || true)
    owners=$(psql_q "select count(*) from martin.owners" || true)
    msgs=$(psql_q "select count(*) from martin.messages" || true)
    echo "  当前     alembic ${ver:-unknown} / owners ${owners:-0} / messages ${msgs:-0}"
    echo
    echo "别忘了："
    echo "  1. agent 侧的 MARTIN_URL 要改指向新服务器的 Hub 地址"
    echo "  2. 如旧服务器有反向代理 / 证书 / 防火墙规则，按原样在新服务器配置"
    echo "  3. 新服务器确认无误后再停用旧服务器"
}

case "${1:-}" in
    export) shift; cmd_export ;;
    import) shift; cmd_import "$@" ;;
    *) usage; exit 1 ;;
esac
