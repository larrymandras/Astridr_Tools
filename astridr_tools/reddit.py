"""Reddit integration tool \u2014 search and browse Reddit via OAuth app-only flow.

Requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET environment variables.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

REDDIT_AUTH_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API_BASE = "https://oauth.reddit.com"


class RedditTool(BaseTool):
    """Search and browse Reddit: subreddits, posts, comments."""

    name = "reddit"
    description = "Search and browse Reddit posts, subreddits, and comments"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "hot", "top", "get_post", "get_comments"],
                "description": "The Reddit operation to perform.",
            },
            "subreddit": {
                "type": "string",
                "description": "Subreddit name (without r/ prefix).",
            },
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "post_id": {
                "type": "string",
                "description": "Reddit post ID (for get_post/get_comments).",
            },
            "time_filter": {
                "type": "string",
                "enum": ["hour", "day", "week", "month", "year", "all"],
                "description": "Time filter for top/search (default: week).",
                "default": "week",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 10, max 100).",
                "default": 10,
            },
            "sort": {
                "type": "string",
                "enum": ["relevance", "hot", "top", "new", "comments"],
                "description": "Sort order for search results.",
                "default": "relevance",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self._client_id = os.environ.get("REDDIT_CLIENT_ID", "")
        self._client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._token_expires_at: float = 0

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"User-Agent": "astridr:v1.0 (by /u/astridr-bot)"},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def _ensure_token(self) -> str | None:
        """Get app-only OAuth bearer token, refreshing if expired."""
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        if not self._client_id or not self._client_secret:
            return None

        client = self._ensure_client()
        try:
            resp = await client.post(
                REDDIT_AUTH_URL,
                auth=(self._client_id, self._client_secret),
                data={"grant_type": "client_credentials"},
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            self._token_expires_at = time.time() + data.get("expires_in", 3600) - 60
            log.debug("reddit.token_acquired")
            return self._access_token
        except httpx.HTTPError as exc:
            log.error("reddit.auth_failed", error=str(exc))
            return None

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        if not self._client_id or not self._client_secret:
            return ToolResult(success=False, error="REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET not configured")

        token = await self._ensure_token()
        if not token:
            return ToolResult(success=False, error="Failed to acquire Reddit OAuth token")

        dispatch = {
            "search": self._search,
            "hot": self._hot,
            "top": self._top,
            "get_post": self._get_post,
            "get_comments": self._get_comments,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("reddit.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Reddit API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("reddit.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _search(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for search")

        subreddit = kwargs.get("subreddit", "")
        url = f"{REDDIT_API_BASE}/r/{subreddit}/search" if subreddit else f"{REDDIT_API_BASE}/search"
        params = {
            "q": query,
            "limit": min(kwargs.get("limit", 10), 100),
            "sort": kwargs.get("sort", "relevance"),
            "t": kwargs.get("time_filter", "week"),
            "restrict_sr": "true" if subreddit else "false",
            "type": "link",
        }
        resp = await client.get(url, params=params, headers=self._auth_headers())
        resp.raise_for_status()
        data = resp.json()
        posts = [c["data"] for c in data.get("data", {}).get("children", [])]
        lines = [
            f"[{p.get('score', 0)}] r/{p.get('subreddit', '')} \u2014 {p.get('title', '')} ({p.get('num_comments', 0)} comments)"
            for p in posts
        ]
        return ToolResult(success=True, output="\n".join(lines), data={"posts": posts})

    async def _hot(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        subreddit = kwargs.get("subreddit", "all")
        limit = min(kwargs.get("limit", 10), 100)
        resp = await client.get(
            f"{REDDIT_API_BASE}/r/{subreddit}/hot",
            params={"limit": limit},
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        posts = [c["data"] for c in data.get("data", {}).get("children", [])]
        lines = [
            f"[{p.get('score', 0)}] {p.get('title', '')} ({p.get('num_comments', 0)} comments)"
            for p in posts
        ]
        return ToolResult(success=True, output="\n".join(lines), data={"posts": posts})

    async def _top(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        subreddit = kwargs.get("subreddit", "all")
        limit = min(kwargs.get("limit", 10), 100)
        time_filter = kwargs.get("time_filter", "week")
        resp = await client.get(
            f"{REDDIT_API_BASE}/r/{subreddit}/top",
            params={"limit": limit, "t": time_filter},
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        posts = [c["data"] for c in data.get("data", {}).get("children", [])]
        lines = [
            f"[{p.get('score', 0)}] {p.get('title', '')} ({p.get('num_comments', 0)} comments)"
            for p in posts
        ]
        return ToolResult(success=True, output="\n".join(lines), data={"posts": posts})

    async def _get_post(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        post_id = kwargs.get("post_id", "")
        if not post_id:
            return ToolResult(success=False, error="post_id is required for get_post")

        resp = await client.get(
            f"{REDDIT_API_BASE}/api/info",
            params={"id": f"t3_{post_id}"},
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        children = data.get("data", {}).get("children", [])
        if not children:
            return ToolResult(success=False, error=f"Post {post_id} not found")
        post = children[0]["data"]
        output = (
            f"r/{post.get('subreddit', '')} \u2014 {post.get('title', '')}\n"
            f"Score: {post.get('score', 0)} | Comments: {post.get('num_comments', 0)}\n"
            f"URL: {post.get('url', '')}\n"
            f"{post.get('selftext', '')[:500]}"
        )
        return ToolResult(success=True, output=output, data={"post": post})

    async def _get_comments(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        post_id = kwargs.get("post_id", "")
        if not post_id:
            return ToolResult(success=False, error="post_id is required for get_comments")

        limit = min(kwargs.get("limit", 10), 100)
        resp = await client.get(
            f"{REDDIT_API_BASE}/comments/{post_id}",
            params={"limit": limit, "depth": 2, "sort": "best"},
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        comments = []
        if len(data) > 1:
            for c in data[1].get("data", {}).get("children", []):
                if c.get("kind") == "t1":
                    comments.append(c["data"])
        lines = [
            f"[{c.get('score', 0)}] u/{c.get('author', '[deleted]')}: {c.get('body', '')[:200]}"
            for c in comments
        ]
        return ToolResult(
            success=True,
            output="\n\n".join(lines),
            data={"comments": comments},
        )
