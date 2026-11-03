"""External API documentation, auto-derived from the live OpenAPI schema.

The single source of truth is the FastAPI route definitions themselves
(path, params, `response_model`, and docstring). This endpoint reshapes
`app.openapi()` into the compact `{groups, endpoints}` contract the
frontend ApiDocsPage consumes, so the docs can never drift from the code.

What is exposed externally is an explicit allowlist, `PUBLIC_ENDPOINTS`
(group → (method, path)). Everything else — UI-internal routes (prefs,
usage, bulk share, tag editing, token management), admin, debug, hooks — is
hidden. Allowlisting by endpoint rather than router tag keeps new internal
routes private by default. Details (params, response, description) still
come from the route itself: add a `response_model` and a docstring there.

The docs data is public (no login): it only describes the API surface; all
listed endpoints themselves still require an API token.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/docs-data", tags=["docs"])

# Group title -> endpoints, in display order. Only these are documented.
PUBLIC_ENDPOINTS: dict[str, list[tuple[str, str]]] = {
    "Videos": [
        ("GET", "/api/videos/ids"),
        ("GET", "/api/videos/{video_id}/meta"),
        ("GET", "/api/videos/{video_id}"),
        ("GET", "/api/videos"),
        ("GET", "/api/tags"),
    ],
    "Content": [
        ("GET", "/api/videos/{video_id}/transcript"),
        ("GET", "/api/videos/{video_id}/subtitles"),
        ("GET", "/api/videos/{video_id}/dialogue"),
        ("GET", "/api/videos/{video_id}/mindmap"),
    ],
    "Analysis": [
        ("POST", "/api/videos/analyze"),
        ("GET", "/api/videos/tasks/{task_id}/stream"),
        ("POST", "/api/videos/tasks/{task_id}/confirm"),
        ("DELETE", "/api/videos/tasks/{task_id}/cancel"),
    ],
    "Tasks": [
        ("GET", "/api/videos/tasks"),
        ("POST", "/api/videos/tasks/{task_id}/retry"),
        ("DELETE", "/api/videos/tasks/{task_id}"),
    ],
}

_AUTH_HEADER = "X-API-Key: <your-api-key>"


def _deref(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref:
        schema = components.get(ref.split("/")[-1], {})
    # Pydantic optionals (`X | None`) render as anyOf/allOf with no top-level
    # type; unwrap to the first non-null branch so types aren't mislabeled.
    branches = schema.get("anyOf") or schema.get("allOf")
    if branches:
        non_null = next((b for b in branches if b.get("type") != "null"), None)
        if non_null is not None:
            return _deref(non_null, components)
    return schema


def _schema_summary(schema: dict[str, Any], components: dict[str, Any]) -> str:
    """Render a compact field summary; recurse one level into array items."""
    schema = _deref(schema, components)
    if schema.get("type") == "array":
        return f"[{_schema_summary(schema.get('items', {}), components)}]"
    props = schema.get("properties")
    if props:
        return "{ " + ", ".join(f'"{name}"' for name in props) + " }"
    return schema.get("type", "object")


def _response_str(operation: dict[str, Any], components: dict[str, Any]) -> str:
    responses = operation.get("responses", {})
    for code in ("200", "201"):
        body = responses.get(code)
        if not body:
            continue
        schema = body.get("content", {}).get("application/json", {}).get("schema")
        return _schema_summary(schema, components) if schema else body.get("description", "OK")
    if "204" in responses:
        return "204 No Content"
    return ""


def _params(operation: dict[str, Any], components: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for p in operation.get("parameters", []):
        out.append({
            "name": p["name"],
            "in": p.get("in", "query"),
            "type": p.get("schema", {}).get("type", "string"),
            "description": p.get("description", ""),
        })
    body_schema = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    body_schema = _deref(body_schema, components)
    for name, prop in body_schema.get("properties", {}).items():
        resolved = _deref(prop, components)
        out.append({
            "name": name,
            "in": "body",
            "type": resolved.get("type", "string"),
            "description": prop.get("description") or resolved.get("description", ""),
        })
    return out


@router.get("")
async def get_api_docs(request: Request) -> dict[str, Any]:
    schema = request.app.openapi()
    components = schema.get("components", {}).get("schemas", {})
    paths = schema.get("paths", {})

    groups = []
    for group, endpoints in PUBLIC_ENDPOINTS.items():
        items = []
        for method, path in endpoints:
            operation = paths.get(path, {}).get(method.lower())
            if operation is None:  # route removed; tests catch this drift
                continue
            items.append({
                "method": method,
                "path": path,
                "description": operation.get("description") or operation.get("summary", ""),
                "params": _params(operation, components),
                "response": _response_str(operation, components),
            })
        if items:
            groups.append({"group": group, "endpoints": items})
    return {"auth_header": _AUTH_HEADER, "base_url": "/api", "groups": groups}
