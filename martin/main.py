from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .cache import build_cache
from .config import Settings, get_settings
from .db import create_schema_and_tables, make_engine, make_sessionmaker
from .deps import make_admin_signer, make_session_signer
from .dispatch import Dispatcher
from .errors import AppError
from .routers import admin, friends, groups, messages, pair, wake, web
from .wake_bus import WakeBus

API_PREFIX = "/api/v1"

ENDPOINTS = {
    "pair": "POST /pair",
    "me": "GET /me | PATCH /me | GET /presence | PUT /presence | DELETE /token",
    "groups": "POST /groups | GET /groups | GET /groups/{id} | PATCH /groups/{id} | POST /groups/{id}/join | POST /groups/{id}/leave | POST /groups/{id}/invite | DELETE /groups/{id}/members/{memberId} | DELETE /groups/{id} | GET /groups/{id}/members | POST /join",
    "messages": "POST /groups/{id}/messages | GET /groups/{id}/messages | DELETE /groups/{id}/messages/{seq} | POST /groups/{id}/read",
    "inbox": "GET /inbox?since=&wait= | GET /probe",
    "web": "POST /web/login | POST /web/logout | GET /web/me | PATCH /web/me | POST /web/password | GET /web/agents | GET /web/groups | POST /web/groups | POST /web/join | POST /web/groups/{id}/join | POST /web/groups/{id}/leave | DELETE /web/groups/{id}/members/{memberId}?with_agents= | POST /web/groups/{id}/archive | DELETE /web/groups/{id} | POST /web/groups/{id}/agents | GET /web/groups/{id}/messages | POST /web/groups/{id}/messages",
    "admin": "POST /admin/login | POST /admin/logout | GET /admin/me | GET /admin/users | POST /admin/users | POST /admin/users/{id}/password | POST /admin/users/{id}/disable | POST /admin/users/{id}/enable | POST /admin/users/{id}/revoke-tokens | GET /admin/stats",
    "friends": "GET /friends | POST /friends | GET /friends/requests | POST /friends/requests/{id} | DELETE /friends/{memberId} | POST /chats",
    "wake": "GET /wake | PUT /wake | DELETE /wake | POST /wake/test | GET /wake/events (SSE) | POST/DELETE /wake/lease",
    "schema": 1,
}


class SPAStaticFiles(StaticFiles):
    """静态文件托管 + SPA 深链接回退。

    命中真实文件直接返回；其余前端路径（/info、/chat/<id>、/join/<code> 等）
    统一返回 index.html，由前端路由解析，刷新或直接打开都不会 404。
    /api 下的未知路径保持 404，不回落到页面。
    """

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response = None
        if response is not None and response.status_code != 404:
            return response
        if path == "api" or path.startswith("api/"):
            raise StarletteHTTPException(status_code=404)
        return await super().get_response("index.html", scope)


def create_app(
    settings: Settings | None = None,
    ensure_schema: bool = True,
    web_dist: Path | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings)
    sessionmaker = make_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if ensure_schema:
            await create_schema_and_tables(engine, settings.db_schema)
        app.state.dispatcher.start()
        yield
        await app.state.dispatcher.stop()
        await engine.dispose()

    app = FastAPI(title="Martin Hub", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.cache = build_cache(settings)
    app.state.session_signer = make_session_signer(settings)
    app.state.admin_signer = make_admin_signer(settings)
    app.state.wake_bus = WakeBus(settings.sse_queue_size)
    app.state.dispatcher = Dispatcher(settings, sessionmaker, app.state.cache, app.state.wake_bus)

    app.include_router(pair.router, prefix=API_PREFIX)
    app.include_router(groups.router, prefix=API_PREFIX)
    app.include_router(messages.router, prefix=API_PREFIX)
    app.include_router(web.router, prefix=API_PREFIX)
    app.include_router(admin.router, prefix=API_PREFIX)
    app.include_router(friends.router, prefix=API_PREFIX)
    app.include_router(wake.router, prefix=API_PREFIX)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal", "message": str(exc)}, "schema": 1},
        )

    @app.get(API_PREFIX)
    async def api_index():
        return {"schema": 1, "endpoints": ENDPOINTS}

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        body = {
            "error": {"code": exc.code, "message": exc.message},
            "schema": 1,
            **exc.payload,
        }
        return JSONResponse(status_code=exc.status_code, content=body)

    dist = web_dist or Path(__file__).resolve().parent.parent / "web" / "dist"
    if dist.is_dir() and (dist / "index.html").is_file():
        # 所有前端路径都由 SPA 接管：/info、/chat/<id>、/join/<code> 等深链接可用
        app.mount("/", SPAStaticFiles(directory=dist, html=True), name="web")

    return app


def main() -> None:
    import uvicorn

    settings = get_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
