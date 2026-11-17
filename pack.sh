#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

if ! command -v zip >/dev/null 2>&1; then
    echo "需要 zip 命令" >&2
    exit 1
fi

name="martin-deploy-$(date +%Y%m%d-%H%M).zip"
rm -f "$name"

find . \
    \( -name .venv -o -name node_modules -o -name __pycache__ -o -name .pytest_cache \
       -o -name .git -o -name .martin -o -name '*.egg-info' -o -path ./web/dist \
       -o -path ./internal -o -path ./.idea -o -path ./.vscode \
       -o -name .pnpm-store -o -name .mypy_cache -o -name .ruff_cache \) -prune -o \
    -type f \
    ! -name '.env' ! -name '.env.bak-*' ! -name '*.dump' ! -name '.DS_Store' \
    ! -name 'martin-deploy-*.zip' ! -name '*.tsbuildinfo' \
    -print | sed 's|^\./||' | zip -q -@ "$name"

count=$(unzip -l "$name" | tail -1 | awk '{print $2}')
echo "已生成 ${name}（${count} 个文件，$(du -h "${name}" | cut -f1)）"
echo
echo "上传："
echo "  scp ${name} user@<服务器>:/opt/martin/"
echo "服务器上解压："
echo "  cd /opt/martin && unzip -o ${name} && rm ${name} && chmod +x deploy.sh docker/entrypoint.sh"
