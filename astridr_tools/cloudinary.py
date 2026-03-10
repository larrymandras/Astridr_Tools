"""Cloudinary integration tool — media asset management and transformation.

Requires CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class CloudinaryTool(BaseTool):
    """Manage Cloudinary media assets: upload, search, transform, delete."""

    name = "cloudinary"
    description = "Upload, search, transform, and manage media assets in Cloudinary"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["upload", "search", "get_asset", "transform_url", "delete"],
                "description": "The Cloudinary operation to perform.",
            },
            "file_url": {
                "type": "string",
                "description": "URL of file to upload (for upload action).",
            },
            "public_id": {
                "type": "string",
                "description": "Public ID of the asset.",
            },
            "resource_type": {
                "type": "string",
                "enum": ["image", "video", "raw", "auto"],
                "description": "Resource type (default: auto).",
                "default": "auto",
            },
            "folder": {
                "type": "string",
                "description": "Folder path for upload.",
            },
            "query": {
                "type": "string",
                "description": "Search expression (Cloudinary search syntax).",
            },
            "transformation": {
                "type": "string",
                "description": "Transformation string (e.g. 'w_300,h_200,c_fill').",
            },
            "max_results": {
                "type": "integer",
                "description": "Max results for search (default 30).",
                "default": 30,
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags to apply to uploaded asset.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset({"search", "get_asset", "transform_url"})

    def __init__(self) -> None:
        self._cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
        self._api_key = os.environ.get("CLOUDINARY_API_KEY", "")
        self._api_secret = os.environ.get("CLOUDINARY_API_SECRET", "")
        self._client: httpx.AsyncClient | None = None

    @property
    def _base_url(self) -> str:
        return f"https://api.cloudinary.com/v1_1/{self._cloud_name}"

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                auth=(self._api_key, self._api_secret),
                timeout=60.0,
            )
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
        if not self._cloud_name or not self._api_key or not self._api_secret:
            return ToolResult(
                success=False,
                error="CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and CLOUDINARY_API_SECRET not configured",
            )

        dispatch = {
            "upload": self._upload,
            "search": self._search,
            "get_asset": self._get_asset,
            "transform_url": self._transform_url,
            "delete": self._delete,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("cloudinary.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Cloudinary API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("cloudinary.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _upload(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        file_url = kwargs.get("file_url", "")
        if not file_url:
            return ToolResult(success=False, error="file_url is required for upload")

        resource_type = kwargs.get("resource_type", "auto")
        payload: dict[str, Any] = {"file": file_url}
        if kwargs.get("public_id"):
            payload["public_id"] = kwargs["public_id"]
        if kwargs.get("folder"):
            payload["folder"] = kwargs["folder"]
        if kwargs.get("tags"):
            payload["tags"] = ",".join(kwargs["tags"])

        resp = await client.post(f"{self._base_url}/{resource_type}/upload", data=payload)
        resp.raise_for_status()
        data = resp.json()
        log.info("cloudinary.uploaded", public_id=data.get("public_id"), url=data.get("secure_url"))
        return ToolResult(
            success=True,
            output=f"Uploaded: {data.get('public_id')} → {data.get('secure_url')}",
            data={"asset": data},
        )

    async def _search(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for search")

        payload = {
            "expression": query,
            "max_results": kwargs.get("max_results", 30),
        }
        resp = await client.post(f"{self._base_url}/resources/search", json=payload)
        resp.raise_for_status()
        data = resp.json()
        resources = data.get("resources", [])
        lines = [
            f"{r.get('public_id', '')} [{r.get('resource_type', '')}] {r.get('secure_url', '')}"
            for r in resources
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"resources": resources, "total_count": data.get("total_count", 0)},
        )

    async def _get_asset(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        public_id = kwargs.get("public_id", "")
        if not public_id:
            return ToolResult(success=False, error="public_id is required for get_asset")

        resource_type = kwargs.get("resource_type", "image")
        resp = await client.get(f"{self._base_url}/resources/{resource_type}/upload/{public_id}")
        resp.raise_for_status()
        asset = resp.json()
        output = (
            f"{asset.get('public_id', '')} [{asset.get('resource_type', '')}]\n"
            f"URL: {asset.get('secure_url', '')}\n"
            f"Size: {asset.get('bytes', 0)} bytes | Format: {asset.get('format', '')}\n"
            f"Dimensions: {asset.get('width', 0)}x{asset.get('height', 0)}"
        )
        return ToolResult(success=True, output=output, data={"asset": asset})

    async def _transform_url(self, **kwargs: Any) -> ToolResult:
        public_id = kwargs.get("public_id", "")
        transformation = kwargs.get("transformation", "")
        if not public_id or not transformation:
            return ToolResult(success=False, error="public_id and transformation are required")

        resource_type = kwargs.get("resource_type", "image")
        url = f"https://res.cloudinary.com/{self._cloud_name}/{resource_type}/upload/{transformation}/{public_id}"
        return ToolResult(
            success=True,
            output=f"Transform URL: {url}",
            data={"url": url, "public_id": public_id, "transformation": transformation},
        )

    async def _delete(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        public_id = kwargs.get("public_id", "")
        if not public_id:
            return ToolResult(success=False, error="public_id is required for delete")

        resource_type = kwargs.get("resource_type", "image")
        resp = await client.post(
            f"{self._base_url}/resources/{resource_type}/upload",
            data={"public_ids[]": public_id},
        )
        resp.raise_for_status()
        log.info("cloudinary.deleted", public_id=public_id)
        return ToolResult(
            success=True,
            output=f"Deleted: {public_id}",
            data={"public_id": public_id, "deleted": True},
        )
