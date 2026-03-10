"""Pixabay integration tool — search free stock images and videos.

Requires PIXABAY_API_KEY environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

PIXABAY_BASE = "https://pixabay.com/api"


class PixabayTool(BaseTool):
    """Search free stock images and videos on Pixabay."""

    name = "pixabay"
    description = "Search free stock images and videos on Pixabay"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search_images", "search_videos"],
                "description": "The Pixabay operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "image_type": {
                "type": "string",
                "enum": ["all", "photo", "illustration", "vector"],
                "description": "Filter by image type.",
                "default": "all",
            },
            "video_type": {
                "type": "string",
                "enum": ["all", "film", "animation"],
                "description": "Filter by video type.",
                "default": "all",
            },
            "orientation": {
                "type": "string",
                "enum": ["all", "horizontal", "vertical"],
                "description": "Image orientation filter.",
                "default": "all",
            },
            "category": {
                "type": "string",
                "enum": [
                    "backgrounds",
                    "fashion",
                    "nature",
                    "science",
                    "education",
                    "feelings",
                    "health",
                    "people",
                    "religion",
                    "places",
                    "animals",
                    "industry",
                    "computer",
                    "food",
                    "sports",
                    "transportation",
                    "travel",
                    "buildings",
                    "business",
                    "music",
                ],
                "description": "Filter by category.",
            },
            "colors": {
                "type": "string",
                "description": "Comma-separated color filter (e.g. 'red,blue').",
            },
            "order": {
                "type": "string",
                "enum": ["popular", "latest"],
                "description": "Sort order.",
                "default": "popular",
            },
            "per_page": {
                "type": "integer",
                "description": "Results per page (3-200).",
                "default": 20,
            },
            "page": {
                "type": "integer",
                "description": "Page number for pagination.",
                "default": 1,
            },
            "safesearch": {
                "type": "boolean",
                "description": "Enable safe search.",
                "default": True,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("PIXABAY_API_KEY", "")
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

        if not self.api_key:
            return ToolResult(success=False, error="PIXABAY_API_KEY not configured")

        dispatch = {
            "search_images": self._search_images,
            "search_videos": self._search_videos,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("pixabay.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Pixabay API error {exc.response.status_code}: {exc.response.text}",
            )
        except httpx.HTTPError as exc:
            log.error("pixabay.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _search_images(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        params: dict[str, Any] = {
            "key": self.api_key,
            "per_page": min(max(kwargs.get("per_page", 20), 3), 200),
            "page": kwargs.get("page", 1),
            "safesearch": str(kwargs.get("safesearch", True)).lower(),
            "order": kwargs.get("order", "popular"),
        }
        if kwargs.get("query"):
            params["q"] = kwargs["query"]
        if kwargs.get("image_type"):
            params["image_type"] = kwargs["image_type"]
        if kwargs.get("orientation") and kwargs["orientation"] != "all":
            params["orientation"] = kwargs["orientation"]
        if kwargs.get("category"):
            params["category"] = kwargs["category"]
        if kwargs.get("colors"):
            params["colors"] = kwargs["colors"]

        resp = await client.get(f"{PIXABAY_BASE}/", params=params)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", [])
        lines = [
            f"id:{h['id']} {h.get('tags', '')} — {h.get('webformatURL', '')}"
            for h in hits
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"hits": hits, "totalHits": data.get("totalHits", 0)},
        )

    async def _search_videos(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        params: dict[str, Any] = {
            "key": self.api_key,
            "per_page": min(max(kwargs.get("per_page", 20), 3), 200),
            "page": kwargs.get("page", 1),
            "safesearch": str(kwargs.get("safesearch", True)).lower(),
            "order": kwargs.get("order", "popular"),
        }
        if kwargs.get("query"):
            params["q"] = kwargs["query"]
        if kwargs.get("video_type"):
            params["video_type"] = kwargs["video_type"]
        if kwargs.get("category"):
            params["category"] = kwargs["category"]

        resp = await client.get(f"{PIXABAY_BASE}/videos/", params=params)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", [])
        lines = [
            f"id:{h['id']} {h.get('tags', '')} — {h.get('duration', 0)}s — {h.get('pageURL', '')}"
            for h in hits
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"hits": hits, "totalHits": data.get("totalHits", 0)},
        )
