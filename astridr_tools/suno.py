"""Suno integration tool — AI music generation.

Requires SUNO_API_KEY environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

SUNO_BASE = "https://studio-api.suno.ai/api"


class SunoTool(BaseTool):
    """Generate AI music with Suno: create songs, extend, check status."""

    name = "suno"
    description = "Generate AI music with Suno — create songs, extend tracks, get status"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["generate", "get_song", "list_songs", "extend"],
                "description": "The Suno operation to perform.",
            },
            "prompt": {
                "type": "string",
                "description": "Text prompt describing the song to generate.",
            },
            "style": {
                "type": "string",
                "description": "Music style/genre (e.g. 'pop', 'jazz', 'electronic').",
            },
            "title": {
                "type": "string",
                "description": "Song title.",
            },
            "lyrics": {
                "type": "string",
                "description": "Custom lyrics for the song.",
            },
            "song_id": {
                "type": "string",
                "description": "Song ID for get_song/extend.",
            },
            "instrumental": {
                "type": "boolean",
                "description": "Generate instrumental only (no vocals).",
                "default": False,
            },
            "duration": {
                "type": "integer",
                "description": "Target duration in seconds for extend.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results for list_songs.",
                "default": 20,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset({"get_song", "list_songs"})

    def __init__(self) -> None:
        self._api_key = os.environ.get("SUNO_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
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
            return ToolResult(success=False, error="SUNO_API_KEY not configured")

        dispatch = {
            "generate": self._generate,
            "get_song": self._get_song,
            "list_songs": self._list_songs,
            "extend": self._extend,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("suno.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Suno API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("suno.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _generate(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        prompt = kwargs.get("prompt", "")
        if not prompt and not kwargs.get("lyrics"):
            return ToolResult(success=False, error="prompt or lyrics is required for generate")

        payload: dict[str, Any] = {
            "make_instrumental": kwargs.get("instrumental", False),
        }
        if kwargs.get("prompt"):
            payload["gpt_description_prompt"] = kwargs["prompt"]
        if kwargs.get("lyrics"):
            payload["prompt"] = kwargs["lyrics"]
        if kwargs.get("style"):
            payload["tags"] = kwargs["style"]
        if kwargs.get("title"):
            payload["title"] = kwargs["title"]

        resp = await client.post(f"{SUNO_BASE}/generate/v2", json=payload)
        resp.raise_for_status()
        data = resp.json()
        clips = data.get("clips", [])
        song_ids = [c.get("id", "") for c in clips]
        log.info("suno.generation_started", song_ids=song_ids)
        lines = [f"Song ID: {sid}" for sid in song_ids]
        return ToolResult(
            success=True,
            output=f"Generation started:\n" + "\n".join(lines) + "\nUse get_song to check status.",
            data={"clips": clips, "song_ids": song_ids, "status": "generating"},
        )

    async def _get_song(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        song_id = kwargs.get("song_id", "")
        if not song_id:
            return ToolResult(success=False, error="song_id is required for get_song")

        resp = await client.get(f"{SUNO_BASE}/feed/{song_id}")
        resp.raise_for_status()
        data = resp.json()
        songs = data if isinstance(data, list) else [data]
        if not songs:
            return ToolResult(success=False, error=f"Song {song_id} not found")

        song = songs[0]
        status = song.get("status", "unknown")
        audio_url = song.get("audio_url", "")
        output = (
            f"Title: {song.get('title', 'Untitled')}\n"
            f"Status: {status}\n"
            f"Style: {song.get('metadata', {}).get('tags', '')}\n"
        )
        if audio_url:
            output += f"Audio: {audio_url}\n"

        return ToolResult(success=True, output=output, data={"song": song})

    async def _list_songs(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        limit = kwargs.get("limit", 20)
        resp = await client.get(f"{SUNO_BASE}/feed", params={"page": 0, "page_size": limit})
        resp.raise_for_status()
        data = resp.json()
        songs = data if isinstance(data, list) else data.get("clips", [])
        lines = [
            f"{s.get('id', '')} — {s.get('title', 'Untitled')} [{s.get('status', '')}]"
            for s in songs
        ]
        return ToolResult(success=True, output="\n".join(lines), data={"songs": songs})

    async def _extend(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        song_id = kwargs.get("song_id", "")
        if not song_id:
            return ToolResult(success=False, error="song_id is required for extend")

        payload: dict[str, Any] = {
            "clip_id": song_id,
        }
        if kwargs.get("prompt"):
            payload["gpt_description_prompt"] = kwargs["prompt"]
        if kwargs.get("lyrics"):
            payload["prompt"] = kwargs["lyrics"]
        if kwargs.get("duration"):
            payload["continue_at"] = kwargs["duration"]

        resp = await client.post(f"{SUNO_BASE}/generate/v2", json=payload)
        resp.raise_for_status()
        data = resp.json()
        clips = data.get("clips", [])
        new_ids = [c.get("id", "") for c in clips]
        log.info("suno.extend_started", original=song_id, new_ids=new_ids)
        return ToolResult(
            success=True,
            output=f"Extension started from {song_id}. New IDs: {', '.join(new_ids)}",
            data={"clips": clips, "new_ids": new_ids, "original_id": song_id},
        )
