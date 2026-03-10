"""Hacker News integration tool \u2014 read-only access to HN and Algolia search.

Public API, no authentication required.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

HN_BASE = "https://hacker-news.firebaseio.com/v0"
ALGOLIA_BASE = "https://hn.algolia.com/api/v1"


class HackerNewsTool(BaseTool):
    """Search and browse Hacker News stories, comments, and users."""

    name = "hackernews"
    description = "Search and browse Hacker News: top/new stories, search, items, users"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "top_stories",
                    "new_stories",
                    "search",
                    "get_item",
                    "get_user",
                ],
                "description": "The Hacker News operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query string (for search action).",
            },
            "item_id": {
                "type": "integer",
                "description": "HN item ID (for get_item action).",
            },
            "username": {
                "type": "string",
                "description": "HN username (for get_user action).",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 10, max 50).",
                "default": 10,
            },
            "page": {
                "type": "integer",
                "description": "Page number for search results (0-indexed).",
                "default": 0,
            },
            "tags": {
                "type": "string",
                "description": "Algolia tag filter (e.g. 'story', 'comment', 'ask_hn', 'show_hn').",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        dispatch = {
            "top_stories": self._top_stories,
            "new_stories": self._new_stories,
            "search": self._search,
            "get_item": self._get_item,
            "get_user": self._get_user,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("hackernews.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"HN API error {exc.response.status_code}: {exc.response.text}",
            )
        except httpx.HTTPError as exc:
            log.error("hackernews.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _top_stories(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        limit = min(kwargs.get("limit", 10), 50)

        resp = await client.get(f"{HN_BASE}/topstories.json")
        resp.raise_for_status()
        ids = resp.json()[:limit]

        stories = await self._fetch_items(client, ids)
        lines = [f"[{s.get('score', 0)}] {s.get('title', '')} ({s.get('url', 'no url')})" for s in stories]
        return ToolResult(success=True, output="\n".join(lines), data={"stories": stories})

    async def _new_stories(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        limit = min(kwargs.get("limit", 10), 50)

        resp = await client.get(f"{HN_BASE}/newstories.json")
        resp.raise_for_status()
        ids = resp.json()[:limit]

        stories = await self._fetch_items(client, ids)
        lines = [f"[{s.get('score', 0)}] {s.get('title', '')} ({s.get('url', 'no url')})" for s in stories]
        return ToolResult(success=True, output="\n".join(lines), data={"stories": stories})

    async def _search(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for search")

        params: dict[str, Any] = {
            "query": query,
            "hitsPerPage": min(kwargs.get("limit", 10), 50),
            "page": kwargs.get("page", 0),
        }
        tags = kwargs.get("tags")
        if tags:
            params["tags"] = tags

        resp = await client.get(f"{ALGOLIA_BASE}/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", [])
        lines = [
            f"[{h.get('points', 0)}] {h.get('title', h.get('comment_text', '')[:80])} "
            f"(id:{h.get('objectID', '')})"
            for h in hits
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"hits": hits, "total": data.get("nbHits", 0)},
        )

    async def _get_item(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        item_id = kwargs.get("item_id")
        if item_id is None:
            return ToolResult(success=False, error="item_id is required for get_item")

        resp = await client.get(f"{HN_BASE}/item/{item_id}.json")
        resp.raise_for_status()
        item = resp.json()
        if item is None:
            return ToolResult(success=False, error=f"Item {item_id} not found")

        output = f"[{item.get('type', '')}] {item.get('title', item.get('text', '')[:200])}"
        return ToolResult(success=True, output=output, data={"item": item})

    async def _get_user(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        username = kwargs.get("username", "")
        if not username:
            return ToolResult(success=False, error="username is required for get_user")

        resp = await client.get(f"{HN_BASE}/user/{username}.json")
        resp.raise_for_status()
        user = resp.json()
        if user is None:
            return ToolResult(success=False, error=f"User {username} not found")

        output = f"{user.get('id', '')} \u2014 karma: {user.get('karma', 0)}, created: {user.get('created', '')}"
        return ToolResult(success=True, output=output, data={"user": user})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _fetch_items(self, client: httpx.AsyncClient, ids: list[int]) -> list[dict[str, Any]]:
        """Fetch multiple HN items in parallel."""
        import asyncio

        async def _get(item_id: int) -> dict[str, Any]:
            resp = await client.get(f"{HN_BASE}/item/{item_id}.json")
            resp.raise_for_status()
            return resp.json() or {}

        return await asyncio.gather(*[_get(i) for i in ids])
