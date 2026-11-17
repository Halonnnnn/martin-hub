import pytest
from httpx import ASGITransport, AsyncClient

from martin.config import Settings
from martin.main import create_app

pytestmark = pytest.mark.asyncio


def _make_dist(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        '<!doctype html><div id="root">spa-index</div>', encoding="utf-8"
    )
    assets = dist / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('asset')", encoding="utf-8")
    return dist


async def test_spa_deep_links_fall_back_to_index(tmp_path):
    app = create_app(
        Settings(
            secret_key="test-secret",
            cache_backend="memory",
            wake_dispatch_enabled=False,
        ),
        ensure_schema=False,
        web_dist=_make_dist(tmp_path),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/", "/info", "/page", "/friends", "/chat/gABC123", "/join/XYZ123"):
            r = await client.get(path)
            assert r.status_code == 200, path
            assert "spa-index" in r.text, path

        r = await client.get("/assets/app.js")
        assert r.status_code == 200
        assert "asset" in r.text

        # API 优先级高于静态托管，未知 API 路径不回落成页面
        r = await client.get("/api/v1")
        assert r.status_code == 200
        assert r.json()["schema"] == 1
        r = await client.get("/api/v1/does-not-exist")
        assert r.status_code == 404
        assert "spa-index" not in r.text
