"""Remotion integration tool — programmatic video rendering.

Requires REMOTION_API_TOKEN environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

REMOTION_BASE = "https://api.remotion.dev"


class RemotionTool(BaseTool):
    """Render templated videos with Remotion: compositions, renders, status."""

    name = "remotion"
    description = "Render programmatic videos using Remotion — list compositions, trigger renders, check status"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["render_video", "get_render_status", "list_compositions", "list_renders"],
                "description": "The Remotion operation to perform.",
            },
            "composition_id": {
                "type": "string",
                "description": "Composition ID to render.",
            },
            "serve_url": {
                "type": "string",
                "description": "URL of the Remotion bundle to render from.",
            },
            "input_props": {
                "type": "object",
                "description": "Input props to pass to the composition.",
            },
            "render_id": {
                "type": "string",
                "description": "Render ID for status check.",
            },
            "codec": {
                "type": "string",
                "enum": ["h264", "h265", "vp8", "vp9", "mp3", "aac", "wav", "prores", "gif"],
                "description": "Video codec (default h264).",
                "default": "h264",
            },
            "output_format": {
                "type": "string",
                "enum": ["mp4", "webm", "mkv", "gif"],
                "description": "Output format (default mp4).",
                "default": "mp4",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset(
        {"get_render_status", "list_compositions", "list_renders"}
    )

    def __init__(self) -> None:
        self._api_token = os.environ.get("REMOTION_API_TOKEN", "")
        self._base_url = os.environ.get("REMOTION_API_URL", REMOTION_BASE)
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self._api_token:
                headers["Authorization"] = f"Bearer {self._api_token}"
            self._client = httpx.AsyncClient(headers=headers, timeout=60.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def is_read_only(self, action: str) -> bool:
        return action in self._READ_ACTIONS

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")
        if not self._api_token:
            return ToolResult(success=False, error="REMOTION_API_TOKEN not configured")

        dispatch = {
            "render_video": self._render_video,
            "get_render_status": self._get_render_status,
            "list_compositions": self._list_compositions,
            "list_renders": self._list_renders,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("remotion.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Remotion API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("remotion.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _render_video(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        composition_id = kwargs.get("composition_id", "")
        serve_url = kwargs.get("serve_url", "")
        if not composition_id or not serve_url:
            return ToolResult(
                success=False, error="composition_id and serve_url are required for render_video"
            )

        payload: dict[str, Any] = {
            "composition": composition_id,
            "serveUrl": serve_url,
            "codec": kwargs.get("codec", "h264"),
            "outputFormat": kwargs.get("output_format", "mp4"),
        }
        if kwargs.get("input_props"):
            payload["inputProps"] = kwargs["input_props"]

        resp = await client.post(f"{self._base_url}/v1/render", json=payload)
        resp.raise_for_status()
        data = resp.json()
        render_id = data.get("renderId", "")
        log.info("remotion.render_started", render_id=render_id)
        return ToolResult(
            success=True,
            output=f"Render started: {render_id}\nUse get_render_status to check progress.",
            data={"render_id": render_id, "status": "processing"},
        )

    async def _get_render_status(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        render_id = kwargs.get("render_id", "")
        if not render_id:
            return ToolResult(success=False, error="render_id is required")

        resp = await client.get(f"{self._base_url}/v1/render/{render_id}")
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "unknown")
        output_url = data.get("outputUrl", "")

        if status == "done" and output_url:
            output = f"Status: done\nOutput URL: {output_url}"
        else:
            progress = data.get("progress", 0)
            output = f"Status: {status}\nProgress: {progress:.0%}" if progress else f"Status: {status}"

        return ToolResult(
            success=True,
            output=output,
            data={"status": status, "render_id": render_id, "output_url": output_url},
        )

    async def _list_compositions(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        resp = await client.get(f"{self._base_url}/v1/compositions")
        resp.raise_for_status()
        data = resp.json()
        compositions = data.get("compositions", [])
        lines = [
            f"{c.get('id', '')} — {c.get('durationInFrames', 0)} frames @ {c.get('fps', 30)}fps"
            for c in compositions
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No compositions found",
            data={"compositions": compositions},
        )

    async def _list_renders(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        resp = await client.get(f"{self._base_url}/v1/renders")
        resp.raise_for_status()
        data = resp.json()
        renders = data.get("renders", [])
        lines = [
            f"{r.get('renderId', '')} — {r.get('status', '')} [{r.get('composition', '')}]"
            for r in renders
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No renders found",
            data={"renders": renders},
        )
