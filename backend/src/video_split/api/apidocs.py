"""Structured API documentation for external consumption."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/docs-data", tags=["docs"])

_API_DOCS = [
    {
        "group": "Videos",
        "endpoints": [
            {
                "method": "GET",
                "path": "/api/videos",
                "description": "List videos owned by the authenticated user.",
                "params": [
                    {"name": "q", "in": "query", "type": "string", "description": "Search title (optional)"},
                    {"name": "tag", "in": "query", "type": "string", "description": "Filter by tag name (optional)"},
                    {"name": "page", "in": "query", "type": "int", "description": "Page number (default 1)"},
                    {"name": "page_size", "in": "query", "type": "int", "description": "Items per page (default 20, max 100)"},
                ],
                "response": '[{ "id", "url", "platform", "title", "thumbnail_url", "duration_seconds", "is_public", "created_at", "tags": [...], "owner_name" }]',
            },
            {
                "method": "GET",
                "path": "/api/videos/public",
                "description": "List all public videos.",
                "params": [
                    {"name": "q", "in": "query", "type": "string", "description": "Search title (optional)"},
                    {"name": "tag", "in": "query", "type": "string", "description": "Filter by tag name (optional)"},
                ],
                "response": "Same as /api/videos",
            },
            {
                "method": "GET",
                "path": "/api/videos/{video_id}",
                "description": "Get full video detail including summary, segments, subtitles, and usage info.",
                "params": [
                    {"name": "video_id", "in": "path", "type": "int", "description": "Video ID"},
                ],
                "response": '{ "id", "url", "platform", "video_id", "title", "thumbnail_url", "upload_date", "duration_seconds", "summary", "summary_en", "usage_json", "is_public", "segments": [{ "segment_index", "title", "title_en", "summary", "summary_en", "start_seconds", "end_seconds" }], "tags": [...] }',
            },
            {
                "method": "GET",
                "path": "/api/videos/{video_id}/subtitles",
                "description": "Get subtitle entries within a time range.",
                "params": [
                    {"name": "video_id", "in": "path", "type": "int", "description": "Video ID"},
                    {"name": "start", "in": "query", "type": "float", "description": "Start seconds"},
                    {"name": "end", "in": "query", "type": "float", "description": "End seconds"},
                ],
                "response": '[{ "start": 0.0, "duration": 3.5, "text": "..." }]',
            },
            {
                "method": "GET",
                "path": "/api/videos/usage-summary",
                "description": "Aggregated ASR and LLM usage across all user videos.",
                "params": [],
                "response": '{ "asr": [{ "model", "total_seconds" }], "llm": [{ "model", "prompt_tokens", "completion_tokens", "total_tokens" }] }',
            },
        ],
    },
    {
        "group": "Tags",
        "endpoints": [
            {
                "method": "GET",
                "path": "/api/tags",
                "description": "List all tags.",
                "params": [],
                "response": '[{ "id", "name", "color" }]',
            },
        ],
    },
    {
        "group": "Tokens",
        "endpoints": [
            {
                "method": "GET",
                "path": "/api/settings/tokens",
                "description": "List API tokens for the authenticated user.",
                "params": [],
                "response": '[{ "id", "name", "key_prefix", "is_active", "last_used_at", "created_at" }]',
            },
            {
                "method": "POST",
                "path": "/api/settings/tokens",
                "description": "Issue a new API token. The full token is returned only once.",
                "params": [
                    {"name": "name", "in": "body", "type": "string", "description": "Token name"},
                ],
                "response": '{ "id", "name", "key_prefix", "full_key", "is_active", "created_at" }',
            },
            {
                "method": "DELETE",
                "path": "/api/settings/tokens/{token_id}",
                "description": "Delete an API token.",
                "params": [
                    {"name": "token_id", "in": "path", "type": "int", "description": "Token ID"},
                ],
                "response": "204 No Content",
            },
        ],
    },
    {
        "group": "Analysis",
        "endpoints": [
            {
                "method": "POST",
                "path": "/api/videos/analyze",
                "description": "Start video analysis (returns SSE stream with progress events).",
                "params": [
                    {"name": "url", "in": "body", "type": "string", "description": "Video URL (YouTube or Bilibili)"},
                ],
                "response": "SSE stream: events with { stage, progress, message, detail }",
            },
        ],
    },
    {
        "group": "Authentication",
        "endpoints": [
            {
                "method": "POST",
                "path": "/api/auth/login",
                "description": "Login and get JWT token.",
                "params": [
                    {"name": "username", "in": "body", "type": "string", "description": "Username"},
                    {"name": "password", "in": "body", "type": "string", "description": "Password"},
                ],
                "response": '{ "access_token", "token_type": "bearer", "role", "username" }',
            },
        ],
    },
]


@router.get("")
async def get_api_docs():
    return {
        "auth_header": "X-API-Key: <your-api-key>",
        "base_url": "/api",
        "groups": _API_DOCS,
    }
