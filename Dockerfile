# syntax=docker/dockerfile:1

FROM node:22-alpine AS web
WORKDIR /web

# 依赖源；国内服务器可在 .env 里设置 NPM_REGISTRY=https://registry.npmmirror.com
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN npm config set registry "$NPM_REGISTRY"

COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 依赖源；海外服务器可在 .env 里覆盖为 https://pypi.org/simple
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

COPY pyproject.toml ./
COPY martin ./martin
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -U pip -i "$PIP_INDEX_URL" \
    && pip install . -i "$PIP_INDEX_URL"

COPY alembic.ini ./
COPY alembic ./alembic
COPY --from=web /web/dist ./web/dist
COPY docker/entrypoint.sh /entrypoint.sh

# 构建上下文里的文件权限不一定可读（例如本地新建的 600 文件）；
# 运行用户是 uid 10001，必须保证 /app 对非 root 可读。
RUN chmod +x /entrypoint.sh \
    && chmod -R a+rX /app \
    && useradd --uid 10001 --shell /usr/sbin/nologin martin

USER martin

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1', timeout=3)"

ENTRYPOINT ["/entrypoint.sh"]
