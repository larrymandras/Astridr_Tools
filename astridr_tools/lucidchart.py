"""Lucidchart integration tool — diagram and document management.

OAuth only. Requires LUCIDCHART_CLIENT_ID + LUCIDCHART_CLIENT_SECRET.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult
from astridr.tools.oauth import OAuthTokenManager

log = structlog.get_logger()

LUCIDCHART_API_BASE = "https://api.lucid.co/v1"


class LucidchartTool(BaseTool):
    """Manage Lucidchart diagrams: create, export, templates."""

    name = "lucidchart"
    description = "Create and manage Lucidchart diagrams — documents, exports, templates"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_documents",
                    "create_document",
                    "get_document",
                    "export_document",
                    "list_templates",
                ],
                "description": "The Lucidchart operation to perform.",
            },
            "document_id": {
                "type": "string",
                "description": "Document ID for operations on existing documents.",
            },
            "title": {
                "type": "string",
                "description": "Title for new document.",
            },
            "template_id": {
                "type": "string",
                "description": "Template ID to base document on.",
            },
            "export_format": {
                "type": "string",
                "enum": ["png", "pdf", "svg"],
                "description": "Export format (default png).",
                "default": "png",
            },
            "query": {
                "type": "string",
                "description": "Search query for documents or templates.",
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
        {"list_documents", "get_document", "list_templates"}
    )

    def __init__(self) -> None:
        self._oauth = OAuthTokenManager(
            provider="lucidchart",
            client_id_env="LUCIDCHART_CLIENT_ID",
            client_secret_env="LUCIDCHART_CLIENT_SECRET",
            token_url="https://api.lucid.co/oauth2/token",
            scopes=["lucidchart.document.app", "offline_access"],
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
                error="Lucidchart OAuth not configured. Run: python -m astridr.tools.oauth_setup lucidchart",
            )

        dispatch = {
            "list_documents": self._list_documents,
            "create_document": self._create_document,
            "get_document": self._get_document,
            "export_document": self._export_document,
            "list_templates": self._list_templates,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("lucidchart.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Lucidchart API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("lucidchart.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _list_documents(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        params: dict[str, Any] = {"limit": kwargs.get("limit", 20)}
        if kwargs.get("query"):
            params["query"] = kwargs["query"]

        resp = await client.get(
            f"{LUCIDCHART_API_BASE}/documents", params=params, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        documents = data.get("documents", [])
        lines = [f"{d.get('id', '')} — {d.get('title', '')}" for d in documents]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No documents found",
            data={"documents": documents},
        )

    async def _create_document(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        title = kwargs.get("title", "")
        if not title:
            return ToolResult(success=False, error="title is required for create_document")

        payload: dict[str, Any] = {"title": title}
        if kwargs.get("template_id"):
            payload["template_id"] = kwargs["template_id"]

        resp = await client.post(
            f"{LUCIDCHART_API_BASE}/documents",
            json=payload,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        document_id = data.get("id", "")
        log.info("lucidchart.document_created", document_id=document_id)
        return ToolResult(
            success=True,
            output=f"Document created: {document_id} — {title}",
            data={"document": data, "document_id": document_id},
        )

    async def _get_document(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        document_id = kwargs.get("document_id", "")
        if not document_id:
            return ToolResult(success=False, error="document_id is required")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(
            f"{LUCIDCHART_API_BASE}/documents/{document_id}", headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        title = data.get("title", "")
        output = f"Document: {document_id}\nTitle: {title}"
        return ToolResult(success=True, output=output, data={"document": data})

    async def _export_document(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        document_id = kwargs.get("document_id", "")
        if not document_id:
            return ToolResult(
                success=False, error="document_id is required for export_document"
            )

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        payload = {"format": kwargs.get("export_format", "png")}
        resp = await client.post(
            f"{LUCIDCHART_API_BASE}/documents/{document_id}/exports",
            json=payload,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        export_id = data.get("id", "")
        log.info("lucidchart.export_started", document_id=document_id, export_id=export_id)
        return ToolResult(
            success=True,
            output=f"Export started: {export_id} for document {document_id}",
            data={"export": data, "export_id": export_id, "document_id": document_id},
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
            f"{LUCIDCHART_API_BASE}/templates", params=params, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        templates = data.get("templates", [])
        lines = [f"{t.get('id', '')} — {t.get('title', '')}" for t in templates]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No templates found",
            data={"templates": templates},
        )
