"""NewsAPI integration tool \u2014 search and browse news headlines.

Requires NEWSAPI_API_KEY environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

NEWSAPI_BASE = "https://newsapi.org/v2"


class NewsAPITool(BaseTool):
    """Search news articles and browse top headlines via NewsAPI."""

    name = "newsapi"
    description = "Search news articles, browse top headlines, and list sources via NewsAPI"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["top_headlines", "everything", "sources"],
                "description": "The NewsAPI operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "country": {
                "type": "string",
                "description": "2-letter ISO country code (e.g. 'us', 'gb').",
            },
            "category": {
                "type": "string",
                "enum": [
                    "business",
                    "entertainment",
                    "general",
                    "health",
                    "science",
                    "sports",
                    "technology",
                ],
                "description": "News category filter.",
            },
            "sources": {
                "type": "string",
                "description": "Comma-separated source IDs.",
            },
            "language": {
                "type": "string",
                "description": "2-letter ISO language code (e.g. 'en').",
            },
            "sort_by": {
                "type": "string",
                "enum": ["relevancy", "popularity", "publishedAt"],
                "description": "Sort order for everything search.",
                "default": "publishedAt",
            },
            "page_size": {
                "type": "integer",
                "description": "Results per page (max 100).",
                "default": 10,
            },
            "page": {
                "type": "integer",
                "description": "Page number for pagination.",
                "default": 1,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("NEWSAPI_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {}
            if self.api_key:
                headers["X-Api-Key"] = self.api_key
            self._client = httpx.AsyncClient(
                base_url=NEWSAPI_BASE,
                headers=headers,
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        if not self.api_key:
            return ToolResult(success=False, error="NEWSAPI_API_KEY not configured")

        dispatch = {
            "top_headlines": self._top_headlines,
            "everything": self._everything,
            "sources": self._sources,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("newsapi.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"NewsAPI error {exc.response.status_code}: {exc.response.text}",
            )
        except httpx.HTTPError as exc:
            log.error("newsapi.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _top_headlines(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        params: dict[str, Any] = {
            "pageSize": min(kwargs.get("page_size", 10), 100),
            "page": kwargs.get("page", 1),
        }
        if kwargs.get("query"):
            params["q"] = kwargs["query"]
        if kwargs.get("country"):
            params["country"] = kwargs["country"]
        if kwargs.get("category"):
            params["category"] = kwargs["category"]
        if kwargs.get("sources"):
            params["sources"] = kwargs["sources"]

        resp = await client.get("/top-headlines", params=params)
        resp.raise_for_status()
        data = resp.json()
        articles = data.get("articles", [])
        lines = [
            f"[{a.get('source', {}).get('name', '')}] {a.get('title', '')} \u2014 {a.get('publishedAt', '')}"
            for a in articles
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"articles": articles, "totalResults": data.get("totalResults", 0)},
        )

    async def _everything(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for everything search")

        params: dict[str, Any] = {
            "q": query,
            "sortBy": kwargs.get("sort_by", "publishedAt"),
            "pageSize": min(kwargs.get("page_size", 10), 100),
            "page": kwargs.get("page", 1),
        }
        if kwargs.get("language"):
            params["language"] = kwargs["language"]
        if kwargs.get("sources"):
            params["sources"] = kwargs["sources"]

        resp = await client.get("/everything", params=params)
        resp.raise_for_status()
        data = resp.json()
        articles = data.get("articles", [])
        lines = [
            f"[{a.get('source', {}).get('name', '')}] {a.get('title', '')} \u2014 {a.get('publishedAt', '')}"
            for a in articles
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"articles": articles, "totalResults": data.get("totalResults", 0)},
        )

    async def _sources(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        params: dict[str, Any] = {}
        if kwargs.get("category"):
            params["category"] = kwargs["category"]
        if kwargs.get("language"):
            params["language"] = kwargs["language"]
        if kwargs.get("country"):
            params["country"] = kwargs["country"]

        resp = await client.get("/top-headlines/sources", params=params)
        resp.raise_for_status()
        data = resp.json()
        sources = data.get("sources", [])
        lines = [f"{s.get('id', '')} \u2014 {s.get('name', '')} ({s.get('category', '')})" for s in sources]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"sources": sources},
        )
