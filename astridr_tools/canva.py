"""Canva integration tool — design creation and management.

OAuth only. Requires CANVA_CLIENT_ID + CANVA_CLIENT_SECRET.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult
from astridr.tools.oauth import OAuthTokenManager

log = structlog.get_logger()

CANVA_API_BASE = "https://api.canva.com/rest/v1"


class CanvaTool(BaseTool):
    """Manage Canva designs: create, export, templates, assets."""

    name = "canva"
    description = "Create and manage Canva designs — templates, exports, asset uploads"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_designs",
                    "create_design",
                    "get_design",
                    "export_design",
                    "list_templates",
                    "upload_asset",
                ],
                "description": "The Canva operation to perform.",
            },
            "design_id": {
                "type": "string",
                "description": "Design ID for operations on existing designs.",
            },
            "title": {
                "type": "string",
                "description": "Title for new design.",
            },
            "template_id": {
                "type": "string",
                "description": "Template ID to base design on.",
            },
            "design_type": {
                "type": "string",
                "description": "Design type (e.g. 'Presentation', 'Poster', 'SocialMedia').",
            },
            "width": {
                "type": "integer",
                "description": "Design width in pixels.",
            },
            "height": {
                "type": "integer",
                "description": "Design height in pixels.",
            },
            "export_format": {
                "type": "string",
                "enum": ["png", "jpg", "pdf", "mp4", "gif"],
                "description": "Export format (default png).",
                "default": "png",
            },
            "asset_url": {
                "type": "string",
                "description": "URL of asset to upload.",
            },
            "asset_name": {
                "type": "string",
                "description": "Display name for uploaded asset.",
            },
            "query": {
                "type": "string",
                "description": "Search query for templates.",
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
        {"list_designs", "get_design", "list_templates"}
    )

    def __init__(self) -> None:
        self._oauth = OAuthTokenManager(
            provider="canva",
            client_id_env="CANVA_CLIENT_ID",
            client_secret_env="CANVA_CLIENT_SECRET",
            token_url="https://api.canva.com/rest/v1/oauth/token",
            scopes=["design:read", "design:write", "asset:read", "asset:write"],
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
        return {"Authorization": f"Bearer {token}"}

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        if not await self._oauth.is_authenticated():
            return ToolResult(
                success=False,
                error="Canva OAuth not configured. Run: python -m astridr.tools.oauth_setup canva",
            )

        dispatch = {
            "list_designs": self._list_designs,
            "create_design": self._create_design,
            "get_design": self._get_design,
            "export_design": self._export_design,
            "list_templates": self._list_templates,
            "upload_asset": self._upload_asset,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("canva.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Canva API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("canva.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _list_designs(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        params: dict[str, Any] = {"limit": kwargs.get("limit", 20)}
        if kwargs.get("query"):
            params["query"] = kwargs["query"]

        resp = await client.get(f"{CANVA_API_BASE}/designs", params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        designs = data.get("items", [])
        lines = [f"{d.get('id', '')} — {d.get('title', '')}" for d in designs]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No designs found",
            data={"designs": designs},
        )

    async def _create_design(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        title = kwargs.get("title", "")
        if not title:
            return ToolResult(success=False, error="title is required for create_design")

        payload: dict[str, Any] = {"title": title}
        if kwargs.get("template_id"):
            payload["template_id"] = kwargs["template_id"]
        if kwargs.get("design_type"):
            payload["design_type"] = kwargs["design_type"]
        if kwargs.get("width") and kwargs.get("height"):
            payload["dimensions"] = {"width": kwargs["width"], "height": kwargs["height"]}

        resp = await client.post(
            f"{CANVA_API_BASE}/designs",
            json=payload,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        design_id = data.get("id", "")
        log.info("canva.design_created", design_id=design_id)
        return ToolResult(
            success=True,
            output=f"Design created: {design_id} — {title}",
            data={"design": data, "design_id": design_id},
        )

    async def _get_design(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        design_id = kwargs.get("design_id", "")
        if not design_id:
            return ToolResult(success=False, error="design_id is required")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(f"{CANVA_API_BASE}/designs/{design_id}", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        title = data.get("title", "")
        output = f"Design: {design_id}\nTitle: {title}"
        return ToolResult(success=True, output=output, data={"design": data})

    async def _export_design(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        design_id = kwargs.get("design_id", "")
        if not design_id:
            return ToolResult(success=False, error="design_id is required for export_design")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        payload = {"format": kwargs.get("export_format", "png")}
        resp = await client.post(
            f"{CANVA_API_BASE}/designs/{design_id}/exports",
            json=payload,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        export_id = data.get("id", "")
        log.info("canva.export_started", design_id=design_id, export_id=export_id)
        return ToolResult(
            success=True,
            output=f"Export started: {export_id} for design {design_id}",
            data={"export": data, "export_id": export_id, "design_id": design_id},
        )

    async def _list_templates(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        params: dict[str, Any] = {"limit": kwargs.get("limit", 20)}
        if kwargs.get("query"):
            params["query"] = kwargs["query"]

        resp = await client.get(
            f"{CANVA_API_BASE}/brand-templates", params=params, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        templates = data.get("items", [])
        lines = [f"{t.get('id', '')} — {t.get('title', '')}" for t in templates]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No templates found",
            data={"templates": templates},
        )

    async def _upload_asset(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        asset_url = kwargs.get("asset_url", "")
        if not asset_url:
            return ToolResult(success=False, error="asset_url is required for upload_asset")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        payload: dict[str, Any] = {"url": asset_url}
        if kwargs.get("asset_name"):
            payload["name"] = kwargs["asset_name"]

        resp = await client.post(
            f"{CANVA_API_BASE}/asset-uploads",
            json=payload,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        asset_id = data.get("id", "")
        log.info("canva.asset_uploaded", asset_id=asset_id)
        return ToolResult(
            success=True,
            output=f"Asset uploaded: {asset_id}",
            data={"asset": data, "asset_id": asset_id},
        )
