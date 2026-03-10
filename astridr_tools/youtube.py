"""YouTube integration tool \u2014 search, manage, and upload YouTube content.

Hybrid auth: YOUTUBE_API_KEY for reads, OAuth for writes.
Requires YOUTUBE_API_KEY for read operations.
Requires YOUTUBE_CLIENT_ID + YOUTUBE_CLIENT_SECRET for write operations (OAuth).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult
from astridr.tools.oauth import OAuthTokenManager

log = structlog.get_logger()

YOUTUBE_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3"


class YouTubeTool(BaseTool):
    """Search and manage YouTube videos: search, analytics, upload."""

    name = "youtube"
    description = "Search, analyze, and manage YouTube videos and channels"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "search",
                    "get_video",
                    "list_channel_videos",
                    "get_analytics",
                    "upload_video",
                    "update_video",
                ],
                "description": "The YouTube operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "video_id": {
                "type": "string",
                "description": "YouTube video ID.",
            },
            "channel_id": {
                "type": "string",
                "description": "YouTube channel ID.",
            },
            "title": {
                "type": "string",
                "description": "Video title (for upload/update).",
            },
            "description": {
                "type": "string",
                "description": "Video description (for upload/update).",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Video tags.",
            },
            "category_id": {
                "type": "string",
                "description": "YouTube category ID (default '22' for People & Blogs).",
                "default": "22",
            },
            "privacy_status": {
                "type": "string",
                "enum": ["public", "private", "unlisted"],
                "description": "Video privacy status.",
                "default": "private",
            },
            "file_path": {
                "type": "string",
                "description": "Local file path for video upload.",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results (default 10, max 50).",
                "default": 10,
            },
            "order": {
                "type": "string",
                "enum": ["relevance", "date", "rating", "viewCount", "title"],
                "description": "Search result ordering.",
                "default": "relevance",
            },
            "start_date": {
                "type": "string",
                "description": "Start date for analytics (YYYY-MM-DD).",
            },
            "end_date": {
                "type": "string",
                "description": "End date for analytics (YYYY-MM-DD).",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset(
        {"search", "get_video", "list_channel_videos", "get_analytics"}
    )

    def __init__(self) -> None:
        self._api_key = os.environ.get("YOUTUBE_API_KEY", "")
        self._oauth = OAuthTokenManager(
            provider="youtube",
            client_id_env="YOUTUBE_CLIENT_ID",
            client_secret_env="YOUTUBE_CLIENT_SECRET",
            token_url="https://oauth2.googleapis.com/token",
            scopes=[
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.force-ssl",
            ],
        )
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        await self._oauth.close()

    def is_read_only(self, action: str) -> bool:
        return action in self._READ_ACTIONS

    async def _get_auth_headers(self, require_oauth: bool = False) -> dict[str, str] | None:
        """Get auth headers. OAuth for writes, API key for reads."""
        if require_oauth:
            token = await self._oauth.get_access_token()
            if token:
                return {"Authorization": f"Bearer {token}"}
            return None
        # For reads, prefer OAuth if available, fall back to API key
        token = await self._oauth.get_access_token()
        if token:
            return {"Authorization": f"Bearer {token}"}
        if self._api_key:
            return {}  # API key passed as query param
        return None

    def _api_key_params(self) -> dict[str, str]:
        """Return API key as query param if no OAuth."""
        if self._api_key:
            return {"key": self._api_key}
        return {}

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        is_write = action in ("upload_video", "update_video")
        if is_write:
            token = await self._oauth.get_access_token()
            if not token:
                return ToolResult(
                    success=False,
                    error="OAuth not configured. Run: python -m astridr.tools.oauth_setup youtube",
                )
        elif not self._api_key and not await self._oauth.is_authenticated():
            return ToolResult(
                success=False,
                error="YOUTUBE_API_KEY not configured and OAuth not set up",
            )

        dispatch = {
            "search": self._search,
            "get_video": self._get_video,
            "list_channel_videos": self._list_channel_videos,
            "get_analytics": self._get_analytics,
            "upload_video": self._upload_video,
            "update_video": self._update_video,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("youtube.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"YouTube API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("youtube.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _search(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for search")

        headers = await self._get_auth_headers() or {}
        params: dict[str, Any] = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(kwargs.get("max_results", 10), 50),
            "order": kwargs.get("order", "relevance"),
            **self._api_key_params(),
        }
        resp = await client.get(f"{YOUTUBE_BASE}/search", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        lines = [
            f"{i['id'].get('videoId', '')} \u2014 {i['snippet'].get('title', '')} "
            f"[{i['snippet'].get('channelTitle', '')}]"
            for i in items
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"items": items, "total": data.get("pageInfo", {}).get("totalResults", 0)},
        )

    async def _get_video(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        video_id = kwargs.get("video_id", "")
        if not video_id:
            return ToolResult(success=False, error="video_id is required")

        headers = await self._get_auth_headers() or {}
        params = {
            "part": "snippet,statistics,contentDetails",
            "id": video_id,
            **self._api_key_params(),
        }
        resp = await client.get(f"{YOUTUBE_BASE}/videos", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return ToolResult(success=False, error=f"Video {video_id} not found")

        video = items[0]
        snippet = video.get("snippet", {})
        stats = video.get("statistics", {})
        output = (
            f"{snippet.get('title', '')}\n"
            f"Channel: {snippet.get('channelTitle', '')}\n"
            f"Views: {stats.get('viewCount', 'N/A')} | "
            f"Likes: {stats.get('likeCount', 'N/A')} | "
            f"Comments: {stats.get('commentCount', 'N/A')}\n"
            f"Published: {snippet.get('publishedAt', '')}"
        )
        return ToolResult(success=True, output=output, data={"video": video})

    async def _list_channel_videos(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        channel_id = kwargs.get("channel_id", "")
        if not channel_id:
            return ToolResult(success=False, error="channel_id is required")

        headers = await self._get_auth_headers() or {}
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "type": "video",
            "maxResults": min(kwargs.get("max_results", 10), 50),
            "order": "date",
            **self._api_key_params(),
        }
        resp = await client.get(f"{YOUTUBE_BASE}/search", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        lines = [
            f"{i['id'].get('videoId', '')} \u2014 {i['snippet'].get('title', '')} "
            f"[{i['snippet'].get('publishedAt', '')[:10]}]"
            for i in items
        ]
        return ToolResult(success=True, output="\n".join(lines), data={"items": items})

    async def _get_analytics(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers(require_oauth=True)
        if not headers:
            return ToolResult(success=False, error="OAuth required for analytics")

        params: dict[str, Any] = {
            "ids": "channel==MINE",
            "metrics": "views,estimatedMinutesWatched,averageViewDuration,subscribersGained",
            "dimensions": "day",
            "sort": "-day",
        }
        if kwargs.get("start_date"):
            params["startDate"] = kwargs["start_date"]
        if kwargs.get("end_date"):
            params["endDate"] = kwargs["end_date"]

        resp = await client.get(
            "https://youtubeanalytics.googleapis.com/v2/reports",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        headers_list = [h.get("name", "") for h in data.get("columnHeaders", [])]
        lines = [", ".join(str(v) for v in row) for row in rows[:14]]
        output = f"Columns: {', '.join(headers_list)}\n" + "\n".join(lines)
        return ToolResult(success=True, output=output, data={"analytics": data})

    async def _upload_video(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        file_path = kwargs.get("file_path", "")
        title = kwargs.get("title", "")
        if not file_path or not title:
            return ToolResult(success=False, error="file_path and title are required for upload")

        headers = await self._get_auth_headers(require_oauth=True)
        if not headers:
            return ToolResult(success=False, error="OAuth required for upload")

        import os as _os

        if not _os.path.isfile(file_path):
            return ToolResult(success=False, error=f"File not found: {file_path}")

        metadata = {
            "snippet": {
                "title": title,
                "description": kwargs.get("description", ""),
                "tags": kwargs.get("tags", []),
                "categoryId": kwargs.get("category_id", "22"),
            },
            "status": {
                "privacyStatus": kwargs.get("privacy_status", "private"),
            },
        }

        import json

        params = {"uploadType": "resumable", "part": "snippet,status"}
        resp = await client.post(
            f"{YOUTUBE_UPLOAD_BASE}/videos",
            params=params,
            headers={**headers, "Content-Type": "application/json"},
            content=json.dumps(metadata),
        )
        resp.raise_for_status()
        upload_url = resp.headers.get("Location", "")

        if not upload_url:
            return ToolResult(success=False, error="Failed to get upload URL")

        with open(file_path, "rb") as f:
            video_data = f.read()

        upload_resp = await client.put(
            upload_url,
            headers={**headers, "Content-Type": "video/*"},
            content=video_data,
            timeout=600.0,
        )
        upload_resp.raise_for_status()
        video = upload_resp.json()
        video_id = video.get("id", "")
        log.info("youtube.video_uploaded", video_id=video_id, title=title)
        return ToolResult(
            success=True,
            output=f"Uploaded: {video_id} \u2014 {title}\nhttps://youtube.com/watch?v={video_id}",
            data={"video": video, "video_id": video_id},
        )

    async def _update_video(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        video_id = kwargs.get("video_id", "")
        if not video_id:
            return ToolResult(success=False, error="video_id is required for update")

        headers = await self._get_auth_headers(require_oauth=True)
        if not headers:
            return ToolResult(success=False, error="OAuth required for update")

        snippet: dict[str, Any] = {"categoryId": kwargs.get("category_id", "22")}
        if kwargs.get("title"):
            snippet["title"] = kwargs["title"]
        if kwargs.get("description"):
            snippet["description"] = kwargs["description"]
        if kwargs.get("tags"):
            snippet["tags"] = kwargs["tags"]

        payload = {"id": video_id, "snippet": snippet}
        if kwargs.get("privacy_status"):
            payload["status"] = {"privacyStatus": kwargs["privacy_status"]}

        parts = ["snippet"]
        if "status" in payload:
            parts.append("status")

        resp = await client.put(
            f"{YOUTUBE_BASE}/videos",
            params={"part": ",".join(parts)},
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()
        video = resp.json()
        log.info("youtube.video_updated", video_id=video_id)
        return ToolResult(
            success=True,
            output=f"Updated video: {video_id}",
            data={"video": video},
        )
