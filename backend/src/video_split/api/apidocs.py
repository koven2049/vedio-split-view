"""External API documentation, auto-derived from the live OpenAPI schema.

The single source of truth is the FastAPI route definitions themselves
(path, params, `response_model`, and docstring). This endpoint reshapes
`app.openapi()` into the compact `{groups, endpoints}` contract the
frontend ApiDocsPage consumes, so the docs can never drift from the code.

To control what is exposed externally, edit `_VISIBLE_GROUPS` (tag → group
name). Endpoints whose router tag is absent here are hidden (e.g. debug,
admin). To improve an endpoint's docs, add a `response_model` and a
docstring on the route — both flow through automatically.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/docs-data", tags=["docs"])

# Router tag -> external group title. Only listed tags are exposed.
_VISIBLE_GROUPS: dict[str, str] = {
    "auth": "Authentication",
    "api-keys": "Tokens",
    "analysis": "Analysis",
    "tasks": "Tasks",
    "videos": "Videos",
    "mindmap": "Mindmap",
    "tags": "Tags",
}

_AUTH_HEADER = "X-API-Key: <your-api-key>"
_HTTP_METHODS = {"get", "post", "put", "delete", "patch"}


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

    grouped: dict[str, list[dict[str, Any]]] = {}
    for path, methods in schema.get("paths", {}).items():
        # /api/settings/api-keys is a decorator alias of /api/settings/tokens
        # (same handlers); document only the /tokens form to avoid duplicates.
        if path.startswith("/api/settings/api-keys"):
            continue
        for method, operation in methods.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            group = next(
                (_VISIBLE_GROUPS[t] for t in operation.get("tags", []) if t in _VISIBLE_GROUPS),
                None,
            )
            if group is None:
                continue
            grouped.setdefault(group, []).append({
                "method": method.upper(),
                "path": path,
                "description": operation.get("description") or operation.get("summary", ""),
                "params": _params(operation, components),
                "response": _response_str(operation, components),
            })

    # Preserve the declared group order from _VISIBLE_GROUPS.
    groups = [
        {"group": name, "endpoints": grouped[name]}
        for name in dict.fromkeys(_VISIBLE_GROUPS.values())
        if name in grouped
    ]
    return {"auth_header": _AUTH_HEADER, "base_url": "/api", "groups": groups}
