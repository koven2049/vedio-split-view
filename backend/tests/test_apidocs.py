"""API docs are auto-derived from the live OpenAPI schema.

WHY these tests: the previous hand-written `_API_DOCS` list drifted out of
sync with the real routes — it described `/analyze` as returning an SSE
stream (it returns a task_id), omitted xiaoyuzhou, and had no Tasks group
at all. These tests pin the contract the frontend ApiDocsPage depends on
AND guard against that drift by asserting facts only a live-schema source
can satisfy.
"""

from __future__ import annotations

from httpx import AsyncClient

_CONTRACT_FIELDS = {"method", "path", "description", "params", "response"}


def _all_endpoints(data: dict) -> list[dict]:
    return [ep for g in data["groups"] for ep in g["endpoints"]]


def _all_paths(data: dict) -> list[str]:
    return [ep["path"] for ep in _all_endpoints(data)]


def _find(data: dict, method: str, path: str) -> dict | None:
    for ep in _all_endpoints(data):
        if ep["method"] == method and ep["path"] == path:
            return ep
    return None


async def test_docs_data_structure(client: AsyncClient):
    """Frontend ApiDocsPage requires {auth_header, base_url, groups[].endpoints[]}."""
    resp = await client.get("/api/docs-data")
    assert resp.status_code == 200
    data = resp.json()
    assert {"auth_header", "base_url", "groups"} <= data.keys()
    for g in data["groups"]:
        assert {"group", "endpoints"} <= g.keys()
        for ep in g["endpoints"]:
            assert _CONTRACT_FIELDS <= ep.keys(), f"endpoint missing fields: {ep}"
            assert isinstance(ep["params"], list)


async def test_analyze_documented_as_async_not_sse(client: AsyncClient):
    """analyze returns a task_id (async), NOT a direct SSE stream — old doc was wrong."""
    data = (await client.get("/api/docs-data")).json()
    analyze = _find(data, "POST", "/api/videos/analyze")
    assert analyze is not None
    blob = (analyze["description"] + " " + analyze["response"]).lower()
    assert "task_id" in blob, f"analyze doc must mention task_id, got: {analyze}"


async def test_analyze_mentions_xiaoyuzhou(client: AsyncClient):
    """All three platforms are supported; doc must not omit xiaoyuzhou."""
    data = (await client.get("/api/docs-data")).json()
    analyze = _find(data, "POST", "/api/videos/analyze")
    assert analyze is not None
    blob = (analyze["description"] + " " + str(analyze["params"])).lower()
    assert "xiaoyuzhou" in blob or "小宇宙" in blob


async def test_internal_endpoints_hidden(client: AsyncClient):
    """debug/admin endpoints must NOT leak into the external-facing API docs."""
    data = (await client.get("/api/docs-data")).json()
    paths = _all_paths(data)
    assert not any(p.startswith("/api/debug") for p in paths)
    assert not any(p.startswith("/api/admin") for p in paths)
    assert not any(p.startswith("/api/hooks") for p in paths)
    assert not any(p.startswith("/api/public") for p in paths)


async def test_external_endpoints_present(client: AsyncClient):
    data = (await client.get("/api/docs-data")).json()
    paths = _all_paths(data)
    assert "/api/videos/analyze" in paths
    assert any("subtitles" in p for p in paths)


async def test_tasks_endpoints_present(client: AsyncClient):
    """Async polling lives under /tasks — the hand-written docs omitted it entirely."""
    data = (await client.get("/api/docs-data")).json()
    paths = _all_paths(data)
    assert any("/tasks" in p for p in paths)


async def test_docs_match_allowlist_exactly(client: AsyncClient):
    """Docs = PUBLIC_ENDPOINTS, nothing more, nothing less.

    Every allowlisted route must still exist (rename/delete drift fails here,
    instead of silently vanishing from the docs).
    """
    from video_split.api.apidocs import PUBLIC_ENDPOINTS

    data = (await client.get("/api/docs-data")).json()
    documented = [(ep["method"], ep["path"]) for ep in _all_endpoints(data)]
    expected = [ep for eps in PUBLIC_ENDPOINTS.values() for ep in eps]
    assert documented == expected


async def test_ui_internal_endpoints_hidden(client: AsyncClient):
    """Front-end-only routes are not part of the external API surface."""
    data = (await client.get("/api/docs-data")).json()
    paths = set(_all_paths(data))
    for internal in (
        "/api/auth/lang", "/api/auth/preferences", "/api/auth/usage-stats",
        "/api/videos/usage-summary", "/api/videos/llm-logs", "/api/videos/bulk-share",
        "/api/settings/tokens", "/api/videos/tasks/recoverable", "/api/videos/public",
    ):
        assert internal not in paths, internal


async def test_id_meta_detail_documented(client: AsyncClient):
    data = (await client.get("/api/docs-data")).json()
    ids = _find(data, "GET", "/api/videos/ids")
    meta = _find(data, "GET", "/api/videos/{video_id}/meta")
    assert ids and "ids" in ids["response"] and "total" in ids["response"]
    assert meta and "segment_count" in meta["response"]
    assert "summary" not in meta["response"]


async def test_docs_data_public_without_login(client: AsyncClient):
    """API docs must be readable without logging in (no auth header)."""
    resp = await client.get("/api/docs-data")
    assert resp.status_code == 200
    assert resp.json()["groups"]


def test_deref_unwraps_optional_anyof():
    """An optional field (int | None) must derive its real type, not fall back to string."""
    from video_split.api.apidocs import _deref

    schema = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    assert _deref(schema, {}).get("type") == "integer"


async def test_response_schema_auto_derived(client: AsyncClient):
    """response is derived from the response_model schema, not hand-typed.

    VideoOut declares a `segments` field, so the derived response summary
    must surface it — proving the deref walked the real Pydantic model.
    """
    data = (await client.get("/api/docs-data")).json()
    detail = _find(data, "GET", "/api/videos/{video_id}")
    assert detail is not None
    assert "segments" in detail["response"]
