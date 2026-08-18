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


async def test_external_endpoints_present(client: AsyncClient):
    data = (await client.get("/api/docs-data")).json()
    paths = _all_paths(data)
    assert "/api/videos/analyze" in paths
    assert any("subtitles" in p for p in paths)
    assert "/api/settings/tokens" in paths


async def test_tasks_endpoints_present(client: AsyncClient):
    """Async polling lives under /tasks — the hand-written docs omitted it entirely."""
    data = (await client.get("/api/docs-data")).json()
    paths = _all_paths(data)
    assert any("/tasks" in p for p in paths)


async def test_token_endpoints_not_duplicated(client: AsyncClient):
    """api_keys routes carry both /api-keys and /tokens decorators; expose only /tokens."""
    data = (await client.get("/api/docs-data")).json()
    paths = _all_paths(data)
    assert not any("/api/settings/api-keys" in p for p in paths)
    assert "/api/settings/tokens" in paths


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
