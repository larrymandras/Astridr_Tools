"""Pexels integration tool — search free stock photos and videos.

Requires PEXELS_API_KEY environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

PEXELS_PHOTOS_BASE = "https://api.pexels.com/v1"
PEXELS_VIDEOS_BASE = "https://api.pexels.com/videos"


class PexelsTool(BaseTool):
    """Search and browse free stock photos and videos on Pexels."""

    name = "pexels"
    description = "Search free stock photos and videos on Pexels"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search_photos", "search_videos", "get_photo", "curated"],
                "description": "The Pexels operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "photo_id": {
                "type": "integer",
                "description": "Pexels photo ID (for get_photo action).",
            },
            "orientation": {
                "type": "string",
                "enum": ["landscape", "portrait", "square"],
                "description": "Photo orientation filter.",
            },
            "size": {
                "type": "string",
                "enum": ["large", "medium", "small"],
                "description": "Minimum photo size filter.",
            },
            "color": {
                "type": "string",
                "description": "Color filter (hex without # or color name).",
            },
            "per_page": {
                "type": "integer",
                "description": "Results per page (max 80).",
                "default": 15,
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
        self.api_key = api_key or os.environ.get("PEXELS_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {}
            if self.api_key:
                headers["Authorization"] = self.api_key
            self._client = httpx.AsyncClient(headers=headers, timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        if not self.api_key:
            return ToolResult(success=False, error="PEXELS_API_KEY not configured")

        dispatch = {
            "search_photos": self._search_photos,
            "search_videos": self._search_videos,
            "get_photo": self._get_photo,
            "curated": self._curated,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("pexels.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Pexels API error {exc.response.status_code}: {exc.response.text}",
            )
        except httpx.HTTPError as exc:
            log.error("pexels.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _search_photos(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for search_photos")

        params: dict[str, Any] = {
            "query": query,
            "per_page": min(kwargs.get("per_page", 15), 80),
            "page": kwargs.get("page", 1),
        }
        if kwargs.get("orientation"):
            params["orientation"] = kwargs["orientation"]
        if kwargs.get("size"):
            params["size"] = kwargs["size"]
        if kwargs.get("color"):
            params["color"] = kwargs["color"]

        resp = await client.get(f"{PEXELS_PHOTOS_BASE}/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        photos = data.get("photos", [])
        lines = [
            f"id:{p['id']} {p.get('alt', '')} — {p['src']['original']}"
            for p in photos
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"photos": photos, "total_results": data.get("total_results", 0)},
        )

    async def _search_videos(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for search_videos")

        params: dict[str, Any] = {
            "query": query,
            "per_page": min(kwargs.get("per_page", 15), 80),
            "page": kwargs.get("page", 1),
        }
        if kwargs.get("orientation"):
            params["orientation"] = kwargs["orientation"]
        if kwargs.get("size"):
            params["size"] = kwargs["size"]

        resp = await client.get(f"{PEXELS_VIDEOS_BASE}/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        videos = data.get("videos", [])
        lines = [
            f"id:{v['id']} {v.get('url', '')} — {v.get('duration', 0)}s"
            for v in videos
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"videos": videos, "total_results": data.get("total_results", 0)},
        )

    async def _get_photo(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        photo_id = kwargs.get("photo_id")
        if photo_id is None:
            return ToolResult(success=False, error="photo_id is required for get_photo")

        resp = await client.get(f"{PEXELS_PHOTOS_BASE}/photos/{photo_id}")
        resp.raise_for_status()
        photo = resp.json()
        output = (
            f"id:{photo['id']} by {photo.get('photographer', '')} — "
            f"{photo.get('alt', '')} — {photo['src']['original']}"
        )
        return ToolResult(success=True, output=output, data={"photo": photo})

    async def _curated(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        params: dict[str, Any] = {
            "per_page": min(kwargs.get("per_page", 15), 80),
            "page": kwargs.get("page", 1),
        }

        resp = await client.get(f"{PEXELS_PHOTOS_BASE}/curated", params=params)
        resp.raise_for_status()
        data = resp.json()
        photos = data.get("photos", [])
        lines = [
            f"id:{p['id']} {p.get('alt', '')} — {p['src']['original']}"
            for p in photos
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"photos": photos},
        )
