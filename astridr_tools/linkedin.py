"""LinkedIn integration tool \u2014 profile, connections, and post management.

OAuth only. Requires LINKEDIN_CLIENT_ID + LINKEDIN_CLIENT_SECRET.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult
from astridr.tools.oauth import OAuthTokenManager

log = structlog.get_logger()

LINKEDIN_API_V2 = "https://api.linkedin.com/v2"
LINKEDIN_API_REST = "https://api.linkedin.com/rest"


class LinkedInTool(BaseTool):
    """Manage LinkedIn: profiles, connections, posts, analytics."""

    name = "linkedin"
    description = "Manage LinkedIn profiles, connections, posts, and analytics"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "get_profile",
                    "search_people",
                    "create_post",
                    "get_post_analytics",
                    "list_connections",
                ],
                "description": "The LinkedIn operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query for people search.",
            },
            "text": {
                "type": "string",
                "description": "Post text content.",
            },
            "media_url": {
                "type": "string",
                "description": "URL of media to attach to post.",
            },
            "visibility": {
                "type": "string",
                "enum": ["PUBLIC", "CONNECTIONS"],
                "description": "Post visibility.",
                "default": "PUBLIC",
            },
            "post_urn": {
                "type": "string",
                "description": "Post URN for analytics (e.g. 'urn:li:share:12345').",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 10).",
                "default": 10,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset(
        {"get_profile", "search_people", "get_post_analytics", "list_connections"}
    )

    def __init__(self) -> None:
        self._oauth = OAuthTokenManager(
            provider="linkedin",
            client_id_env="LINKEDIN_CLIENT_ID",
            client_secret_env="LINKEDIN_CLIENT_SECRET",
            token_url="https://www.linkedin.com/oauth/v2/accessToken",
            scopes=["r_liteprofile", "r_organization_social", "w_member_social", "r_basicprofile"],
        )
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        await self._oauth.close()

    def is_read_only(self, action: str) -> bool:
        return action in self._READ_ACTIONS

    async def _get_auth_headers(self) -> dict[str, str] | None:
        token = await self._oauth.get_access_token()
        if not token:
            return None
        return {
            "Authorization": f"Bearer {token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "LinkedIn-Version": "202401",
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        if not await self._oauth.is_authenticated():
            return ToolResult(
                success=False,
                error="LinkedIn OAuth not configured. Run: python -m astridr.tools.oauth_setup linkedin",
            )

        dispatch = {
            "get_profile": self._get_profile,
            "search_people": self._search_people,
            "create_post": self._create_post,
            "get_post_analytics": self._get_post_analytics,
            "list_connections": self._list_connections,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("linkedin.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"LinkedIn API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("linkedin.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _get_profile(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(f"{LINKEDIN_API_V2}/me", headers=headers)
        resp.raise_for_status()
        profile = resp.json()
        first = profile.get("localizedFirstName", "")
        last = profile.get("localizedLastName", "")
        output = f"{first} {last} (ID: {profile.get('id', '')})"
        return ToolResult(success=True, output=output, data={"profile": profile})

    async def _search_people(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for search_people")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        limit = min(kwargs.get("limit", 10), 50)
        params = {
            "q": "people",
            "query": query,
            "count": limit,
        }
        resp = await client.get(f"{LINKEDIN_API_REST}/search", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        elements = data.get("elements", [])
        lines = [
            f"{e.get('title', {}).get('text', '')} \u2014 {e.get('headline', {}).get('text', '')}"
            for e in elements
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No results found",
            data={"results": elements},
        )

    async def _create_post(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        text = kwargs.get("text", "")
        if not text:
            return ToolResult(success=False, error="text is required for create_post")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        # Get author URN
        me_resp = await client.get(f"{LINKEDIN_API_V2}/me", headers=headers)
        me_resp.raise_for_status()
        author_id = me_resp.json().get("id", "")
        author_urn = f"urn:li:person:{author_id}"

        visibility = kwargs.get("visibility", "PUBLIC")
        payload: dict[str, Any] = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": visibility
            },
        }

        resp = await client.post(
            f"{LINKEDIN_API_V2}/ugcPosts",
            json=payload,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        post_data = resp.json()
        post_id = post_data.get("id", "")
        log.info("linkedin.post_created", post_id=post_id)
        return ToolResult(
            success=True,
            output=f"Post created: {post_id}",
            data={"post": post_data, "post_id": post_id},
        )

    async def _get_post_analytics(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        post_urn = kwargs.get("post_urn", "")
        if not post_urn:
            return ToolResult(success=False, error="post_urn is required for get_post_analytics")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(
            f"{LINKEDIN_API_V2}/socialActions/{post_urn}",
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        likes = data.get("likesSummary", {}).get("totalLikes", 0)
        comments = data.get("commentsSummary", {}).get("totalFirstLevelComments", 0)
        output = f"Post: {post_urn}\nLikes: {likes} | Comments: {comments}"
        return ToolResult(
            success=True,
            output=output,
            data={"analytics": data, "likes": likes, "comments": comments},
        )

    async def _list_connections(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        limit = min(kwargs.get("limit", 10), 50)
        resp = await client.get(
            f"{LINKEDIN_API_V2}/connections",
            params={"q": "viewer", "start": 0, "count": limit},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        elements = data.get("elements", [])
        total = data.get("paging", {}).get("total", len(elements))
        lines = [f"Connection: {e.get('to', '')}" for e in elements]
        return ToolResult(
            success=True,
            output=f"Connections ({total} total):\n" + "\n".join(lines),
            data={"connections": elements, "total": total},
        )
