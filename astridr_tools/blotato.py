"""Blotato integration tool — social media scheduling.

Requires BLOTATO_API_KEY environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

BLOTATO_BASE = "https://api.blotato.com/v1"


class BlotatoTool(BaseTool):
    """Schedule and manage social media posts with Blotato."""

    name = "blotato"
    description = "Schedule social media posts across platforms — create, list, cancel posts"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "schedule_post",
                    "list_scheduled",
                    "get_post_status",
                    "list_platforms",
                    "cancel_post",
                ],
                "description": "The Blotato operation to perform.",
            },
            "post_id": {
                "type": "string",
                "description": "Post ID for status check or cancellation.",
            },
            "text": {
                "type": "string",
                "description": "Post text content.",
            },
            "media_url": {
                "type": "string",
                "description": "URL of media to attach to the post.",
            },
            "platform": {
                "type": "string",
                "description": "Target platform (e.g. 'twitter', 'linkedin', 'instagram').",
            },
            "scheduled_at": {
                "type": "string",
                "description": "Scheduled publish time in ISO 8601 format.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 20).",
                "default": 20,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset(
        {"list_scheduled", "get_post_status", "list_platforms"}
    )

    def __init__(self) -> None:
        self._api_key = os.environ.get("BLOTATO_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(headers=headers, timeout=30.0)
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
            return ToolResult(success=False, error="BLOTATO_API_KEY not configured")

        dispatch = {
            "schedule_post": self._schedule_post,
            "list_scheduled": self._list_scheduled,
            "get_post_status": self._get_post_status,
            "list_platforms": self._list_platforms,
            "cancel_post": self._cancel_post,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("blotato.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Blotato API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("blotato.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _schedule_post(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        text = kwargs.get("text", "")
        platform = kwargs.get("platform", "")
        if not text or not platform:
            return ToolResult(
                success=False, error="text and platform are required for schedule_post"
            )

        payload: dict[str, Any] = {
            "text": text,
            "platform": platform,
        }
        if kwargs.get("media_url"):
            payload["media_url"] = kwargs["media_url"]
        if kwargs.get("scheduled_at"):
            payload["scheduled_at"] = kwargs["scheduled_at"]

        resp = await client.post(f"{BLOTATO_BASE}/posts", json=payload)
        resp.raise_for_status()
        data = resp.json()
        post_id = data.get("id", "")
        log.info("blotato.post_scheduled", post_id=post_id, platform=platform)
        return ToolResult(
            success=True,
            output=f"Post scheduled: {post_id} on {platform}",
            data={"post": data, "post_id": post_id},
        )

    async def _list_scheduled(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        params: dict[str, Any] = {"limit": kwargs.get("limit", 20)}
        resp = await client.get(f"{BLOTATO_BASE}/posts", params=params)
        resp.raise_for_status()
        data = resp.json()
        posts = data.get("posts", [])
        lines = [
            f"{p.get('id', '')} — [{p.get('platform', '')}] {p.get('status', '')} — {p.get('text', '')[:50]}"
            for p in posts
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No scheduled posts",
            data={"posts": posts},
        )

    async def _get_post_status(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        post_id = kwargs.get("post_id", "")
        if not post_id:
            return ToolResult(success=False, error="post_id is required")

        resp = await client.get(f"{BLOTATO_BASE}/posts/{post_id}")
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "unknown")
        platform = data.get("platform", "")
        output = f"Post {post_id}: {status} on {platform}"
        return ToolResult(
            success=True,
            output=output,
            data={"post": data, "status": status},
        )

    async def _list_platforms(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        resp = await client.get(f"{BLOTATO_BASE}/platforms")
        resp.raise_for_status()
        data = resp.json()
        platforms = data.get("platforms", [])
        lines = [
            f"{p.get('id', '')} — {p.get('name', '')} ({p.get('status', '')})"
            for p in platforms
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No platforms connected",
            data={"platforms": platforms},
        )

    async def _cancel_post(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        post_id = kwargs.get("post_id", "")
        if not post_id:
            return ToolResult(success=False, error="post_id is required for cancel_post")

        resp = await client.delete(f"{BLOTATO_BASE}/posts/{post_id}")
        resp.raise_for_status()
        log.info("blotato.post_cancelled", post_id=post_id)
        return ToolResult(
            success=True,
            output=f"Post cancelled: {post_id}",
            data={"post_id": post_id, "cancelled": True},
        )
