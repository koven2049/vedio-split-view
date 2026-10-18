"""Process-mode SPA serving: /assets caching, SPA fallback, API 404 semantics."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from video_split.main import create_app


@pytest.fixture
def dist(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "vite.svg").write_text("<svg/>", encoding="utf-8")
    monkeypatch.setenv("VSLP_FRONTEND_DIST", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_spa_fallback_serves_index_html(client, dist):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/library")
    assert r.status_code == 200
    assert "SPA" in r.text
    assert r.headers["cache-control"] == "no-cache, no-store, must-revalidate"


@pytest.mark.asyncio
async def test_assets_get_immutable_cache(client, dist):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.asyncio
async def test_root_files_served_with_no_cache(client, dist):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/vite.svg")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache, no-store, must-revalidate"


@pytest.mark.asyncio
async def test_unknown_api_paths_keep_404_json(client, dist):
    """The SPA fallback must never swallow API 404 semantics (nginx parity)."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    assert "SPA" not in r.text


@pytest.mark.asyncio
async def test_health_and_docs_unaffected(client, dist):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        assert (await ac.get("/api/health")).status_code == 200
        assert (await ac.get("/docs")).status_code == 200
        assert (await ac.get("/openapi.json")).status_code == 200


@pytest.mark.asyncio
async def test_no_dist_is_noop(client, monkeypatch, tmp_path):
    monkeypatch.setenv("VSLP_FRONTEND_DIST", str(tmp_path / "missing"))
    app = create_app()
    paths = [getattr(r, "path", "") for r in app.routes]
    assert "/{full_path:path}" not in paths
