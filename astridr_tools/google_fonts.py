"""Google Fonts integration tool \u2014 browse and search fonts.

GOOGLE_FONTS_API_KEY is optional but recommended for higher rate limits.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

GOOGLE_FONTS_BASE = "https://www.googleapis.com/webfonts/v1/webfonts"


class GoogleFontsTool(BaseTool):
    """Browse and search Google Fonts."""

    name = "google_fonts"
    description = "Browse and search Google Fonts: list fonts, get font details"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_fonts", "get_font"],
                "description": "The Google Fonts operation to perform.",
            },
            "family": {
                "type": "string",
                "description": "Font family name (for get_font action).",
            },
            "sort": {
                "type": "string",
                "enum": ["alpha", "date", "popularity", "style", "trending"],
                "description": "Sort order for list_fonts.",
                "default": "popularity",
            },
            "category": {
                "type": "string",
                "enum": ["serif", "sans-serif", "display", "handwriting", "monospace"],
                "description": "Filter by font category.",
            },
            "subset": {
                "type": "string",
                "description": "Filter by character subset (e.g. 'latin', 'greek', 'cyrillic').",
            },
            "limit": {
                "type": "integer",
                "description": "Max fonts to return (default 20).",
                "default": 20,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("GOOGLE_FONTS_API_KEY", "")
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
            "list_fonts": self._list_fonts,
            "get_font": self._get_font,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("google_fonts.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Google Fonts API error {exc.response.status_code}: {exc.response.text}",
            )
        except httpx.HTTPError as exc:
            log.error("google_fonts.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _list_fonts(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        params: dict[str, Any] = {
            "sort": kwargs.get("sort", "popularity"),
        }
        if self.api_key:
            params["key"] = self.api_key
        if kwargs.get("subset"):
            params["subset"] = kwargs["subset"]

        resp = await client.get(GOOGLE_FONTS_BASE, params=params)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])

        # Apply category filter client-side (API doesn't support it directly)
        category = kwargs.get("category")
        if category:
            items = [f for f in items if f.get("category") == category]

        limit = kwargs.get("limit", 20)
        items = items[:limit]

        lines = [
            f"{f['family']} ({f.get('category', '')}) \u2014 {len(f.get('variants', []))} variants"
            for f in items
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"fonts": items},
        )

    async def _get_font(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        family = kwargs.get("family", "")
        if not family:
            return ToolResult(success=False, error="family is required for get_font")

        params: dict[str, Any] = {
            "family": family,
        }
        if self.api_key:
            params["key"] = self.api_key

        resp = await client.get(GOOGLE_FONTS_BASE, params=params)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])

        # Find exact match
        match = None
        for font in items:
            if font.get("family", "").lower() == family.lower():
                match = font
                break

        if not match and items:
            match = items[0]

        if not match:
            return ToolResult(success=False, error=f"Font '{family}' not found")

        output = (
            f"{match['family']} ({match.get('category', '')})\n"
            f"Variants: {', '.join(match.get('variants', []))}\n"
            f"Subsets: {', '.join(match.get('subsets', []))}"
        )
        return ToolResult(success=True, output=output, data={"font": match})
