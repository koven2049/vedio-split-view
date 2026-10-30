"""T28 mitigation: gzip responses that cross the cloudflared tunnel.

The public entry rides a cross-border tunnel while the app itself serves
in 1-2ms locally — compression shrinks the slow leg's payload. SSE
streams must stay identity-encoded: starlette's GZipMiddleware excludes
``text/event-stream`` by default; these tests pin that so a starlette
upgrade cannot silently start compressing progress streams.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.conftest import get_admin_token
from video_split.main import create_app
from video_split.models import Task, User

GZIP_HEADERS = {"Accept-Encoding": "gzip"}


@pytest.fixture
def dist(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index-abc123.js").write_text("console.log(1);\n" * 100, encoding="utf-8")
    monkeypatch.setenv("VSLP_FRONTEND_DIST", str(tmp_path))
    return tmp_path


async def test_large_json_response_is_gzipped(client):
    plain = await client.get("/openapi.json")
    assert plain.status_code == 200
    assert len(plain.content) > 1024

    compressed = await client.get("/openapi.json", headers=GZIP_HEADERS)
    assert compressed.status_code == 200
    # starlette only labels when compression actually shrank the body;
    # httpx transparently decodes, so equality proves integrity.
    assert compressed.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in compressed.headers.get("vary", "")
    assert compressed.content == plain.content


async def test_small_json_response_not_compressed(client):
    resp = await client.get("/api/health", headers=GZIP_HEADERS)
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_asset_files_gzipped_with_immutable_cache(client, dist):
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        plain = await ac.get("/assets/index-abc123.js")
        compressed = await ac.get("/assets/index-abc123.js", headers=GZIP_HEADERS)

    assert compressed.status_code == 200
    assert compressed.headers["content-encoding"] == "gzip"
    assert compressed.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert compressed.content == plain.content


@pytest.mark.asyncio
async def test_sse_stream_stays_identity(client, db_engine, monkeypatch):
    """SSE must never be compressed: tiny per-event payloads gain nothing
    and a gzipped long-lived stream risks buffering in tunnels/proxies."""
    async with async_sessionmaker(db_engine, expire_on_commit=False)() as session:
        admin = (
            await session.execute(select(User).where(User.username == "admin"))
        ).scalar_one()
        session.add(
            Task(user_id=admin.id, url="https://example.com/v", platform="youtube")
        )
        await session.commit()

    class _FakeRunner:
        def subscribe(self, task_id: int):
            queue: asyncio.Queue = asyncio.Queue()
            queue.put_nowait({"event": "progress", "data": '{"stage":"metadata"}'})
            queue.put_nowait(None)
            return ([{"event": "progress", "data": '{"stage":"download"}'}], queue)

        def unsubscribe(self, task_id: int, queue: asyncio.Queue) -> None:
            pass

    import video_split.api.tasks as tasks_api

    monkeypatch.setattr(tasks_api, "runner", _FakeRunner())

    token = await get_admin_token(client)
    lines = []
    async with client.stream(
        "GET",
        "/api/videos/tasks/1/stream",
        headers={"Authorization": f"Bearer {token}", **GZIP_HEADERS},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in resp.headers
        async for line in resp.aiter_lines():
            lines.append(line)

    text = "\n".join(lines)
    assert "download" in text
    assert "metadata" in text
