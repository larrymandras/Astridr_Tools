"""HeyGen integration tool — AI avatar video generation.

Requires HEYGEN_API_KEY environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

HEYGEN_BASE = "https://api.heygen.com/v2"


class HeyGenTool(BaseTool):
    """Create AI avatar videos with HeyGen: avatars, voices, templates."""

    name = "heygen"
    description = "Create AI avatar videos using HeyGen — list avatars, voices, create videos"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_avatars", "list_voices", "create_video", "get_video_status", "list_templates"],
                "description": "The HeyGen operation to perform.",
            },
            "avatar_id": {
                "type": "string",
                "description": "Avatar ID for video creation.",
            },
            "voice_id": {
                "type": "string",
                "description": "Voice ID for video creation.",
            },
            "script": {
                "type": "string",
                "description": "Script text for the avatar to speak.",
            },
            "video_id": {
                "type": "string",
                "description": "Video ID for status check.",
            },
            "template_id": {
                "type": "string",
                "description": "Template ID for template-based video creation.",
            },
            "title": {
                "type": "string",
                "description": "Video title.",
            },
            "background_color": {
                "type": "string",
                "description": "Background color hex (e.g. '#ffffff').",
                "default": "#ffffff",
            },
            "ratio": {
                "type": "string",
                "enum": ["16:9", "9:16", "1:1"],
                "description": "Video aspect ratio.",
                "default": "16:9",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset(
        {"list_avatars", "list_voices", "get_video_status", "list_templates"}
    )

    def __init__(self) -> None:
        self._api_key = os.environ.get("HEYGEN_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self._api_key:
                headers["X-Api-Key"] = self._api_key
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
        if not self._api_key:
            return ToolResult(success=False, error="HEYGEN_API_KEY not configured")

        dispatch = {
            "list_avatars": self._list_avatars,
            "list_voices": self._list_voices,
            "create_video": self._create_video,
            "get_video_status": self._get_video_status,
            "list_templates": self._list_templates,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("heygen.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"HeyGen API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("heygen.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _list_avatars(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        resp = await client.get(f"{HEYGEN_BASE}/avatars")
        resp.raise_for_status()
        data = resp.json()
        avatars = data.get("data", {}).get("avatars", [])
        lines = [f"{a.get('avatar_id', '')} — {a.get('avatar_name', '')}" for a in avatars]
        return ToolResult(success=True, output="\n".join(lines), data={"avatars": avatars})

    async def _list_voices(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        resp = await client.get(f"{HEYGEN_BASE}/voices")
        resp.raise_for_status()
        data = resp.json()
        voices = data.get("data", {}).get("voices", [])
        lines = [
            f"{v.get('voice_id', '')} — {v.get('display_name', '')} [{v.get('language', '')}]"
            for v in voices
        ]
        return ToolResult(success=True, output="\n".join(lines), data={"voices": voices})

    async def _create_video(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        script = kwargs.get("script", "")
        if not script:
            return ToolResult(success=False, error="script is required for create_video")

        avatar_id = kwargs.get("avatar_id", "")
        voice_id = kwargs.get("voice_id", "")
        if not avatar_id or not voice_id:
            return ToolResult(success=False, error="avatar_id and voice_id are required")

        payload: dict[str, Any] = {
            "video_inputs": [
                {
                    "character": {
                        "type": "avatar",
                        "avatar_id": avatar_id,
                        "avatar_style": "normal",
                    },
                    "voice": {
                        "type": "text",
                        "input_text": script,
                        "voice_id": voice_id,
                    },
                    "background": {
                        "type": "color",
                        "value": kwargs.get("background_color", "#ffffff"),
                    },
                }
            ],
            "dimension": {"width": 1920, "height": 1080}
            if kwargs.get("ratio", "16:9") == "16:9"
            else {"width": 1080, "height": 1920}
            if kwargs.get("ratio") == "9:16"
            else {"width": 1080, "height": 1080},
        }
        if kwargs.get("title"):
            payload["title"] = kwargs["title"]

        resp = await client.post(f"{HEYGEN_BASE}/video/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()
        video_id = data.get("data", {}).get("video_id", "")
        log.info("heygen.video_created", video_id=video_id)
        return ToolResult(
            success=True,
            output=f"Video generation started: {video_id}\nUse get_video_status to check progress.",
            data={"video_id": video_id, "status": "processing"},
        )

    async def _get_video_status(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        video_id = kwargs.get("video_id", "")
        if not video_id:
            return ToolResult(success=False, error="video_id is required")

        resp = await client.get(f"{HEYGEN_BASE}/video_status.get", params={"video_id": video_id})
        resp.raise_for_status()
        data = resp.json()
        status_data = data.get("data", {})
        status = status_data.get("status", "unknown")
        video_url = status_data.get("video_url", "")

        if status == "completed" and video_url:
            output = f"Status: completed\nVideo URL: {video_url}"
        else:
            output = f"Status: {status}"

        return ToolResult(
            success=True,
            output=output,
            data={"status": status, "video_id": video_id, "video_url": video_url},
        )

    async def _list_templates(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        resp = await client.get(f"{HEYGEN_BASE}/templates")
        resp.raise_for_status()
        data = resp.json()
        templates = data.get("data", {}).get("templates", [])
        lines = [f"{t.get('template_id', '')} — {t.get('name', '')}" for t in templates]
        return ToolResult(success=True, output="\n".join(lines), data={"templates": templates})
