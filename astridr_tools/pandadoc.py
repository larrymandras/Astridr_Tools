"""PandaDoc integration tool — manage documents, templates, and sending.

Requires PANDADOC_API_KEY environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

PANDADOC_BASE = "https://api.pandadoc.com/public/v1"


class PandaDocTool(BaseTool):
    """Manage PandaDoc documents: list, create from template, send."""

    name = "pandadoc"
    description = "List, create, and send PandaDoc documents"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_documents",
                    "get_document",
                    "get_document_status",
                    "create_from_template",
                    "send_document",
                ],
                "description": "The PandaDoc operation to perform.",
            },
            "document_id": {
                "type": "string",
                "description": "PandaDoc document ID.",
            },
            "template_id": {
                "type": "string",
                "description": "Template ID for create_from_template.",
            },
            "name": {
                "type": "string",
                "description": "Document name for create_from_template.",
            },
            "recipients": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "first_name": {"type": "string"},
                        "last_name": {"type": "string"},
                        "role": {"type": "string"},
                    },
                },
                "description": "Recipients for document creation/sending.",
            },
            "fields": {
                "type": "object",
                "description": "Field values to populate in the template.",
            },
            "message": {
                "type": "string",
                "description": "Message to include when sending.",
            },
            "subject": {
                "type": "string",
                "description": "Email subject when sending.",
            },
            "status": {
                "type": "string",
                "enum": ["document.draft", "document.sent", "document.completed", "document.viewed"],
                "description": "Filter by document status.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 50).",
                "default": 50,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset(
        {"list_documents", "get_document", "get_document_status"}
    )

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("PANDADOC_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
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
        if not self.api_key:
            return ToolResult(success=False, error="PANDADOC_API_KEY not configured")

        dispatch = {
            "list_documents": self._list_documents,
            "get_document": self._get_document,
            "get_document_status": self._get_document_status,
            "create_from_template": self._create_from_template,
            "send_document": self._send_document,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("pandadoc.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"PandaDoc API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("pandadoc.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _list_documents(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        params: dict[str, Any] = {"count": kwargs.get("limit", 50)}
        if kwargs.get("status"):
            params["status__eq"] = kwargs["status"]
        resp = await client.get(f"{PANDADOC_BASE}/documents", params=params)
        resp.raise_for_status()
        data = resp.json()
        docs = data.get("results", [])
        lines = [f"{d['id']} — {d.get('name', '')} [{d.get('status', '')}]" for d in docs]
        return ToolResult(success=True, output="\n".join(lines), data={"documents": docs})

    async def _get_document(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        doc_id = kwargs.get("document_id", "")
        if not doc_id:
            return ToolResult(success=False, error="document_id is required")
        resp = await client.get(f"{PANDADOC_BASE}/documents/{doc_id}/details")
        resp.raise_for_status()
        doc = resp.json()
        output = f"{doc.get('name', '')} [{doc.get('status', '')}] — {doc.get('date_created', '')}"
        return ToolResult(success=True, output=output, data={"document": doc})

    async def _get_document_status(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        doc_id = kwargs.get("document_id", "")
        if not doc_id:
            return ToolResult(success=False, error="document_id is required")
        resp = await client.get(f"{PANDADOC_BASE}/documents/{doc_id}")
        resp.raise_for_status()
        doc = resp.json()
        return ToolResult(
            success=True,
            output=f"Status: {doc.get('status', 'unknown')}",
            data={"status": doc.get("status"), "document_id": doc_id},
        )

    async def _create_from_template(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        template_id = kwargs.get("template_id", "")
        name = kwargs.get("name", "")
        if not template_id or not name:
            return ToolResult(success=False, error="template_id and name are required")

        payload: dict[str, Any] = {
            "name": name,
            "template_uuid": template_id,
        }
        if kwargs.get("recipients"):
            payload["recipients"] = kwargs["recipients"]
        if kwargs.get("fields"):
            payload["fields"] = kwargs["fields"]

        resp = await client.post(f"{PANDADOC_BASE}/documents", json=payload)
        resp.raise_for_status()
        doc = resp.json()
        log.info("pandadoc.document_created", doc_id=doc.get("id"), name=name)
        return ToolResult(
            success=True,
            output=f"Created document: {doc.get('id')} — {name}",
            data={"document": doc},
        )

    async def _send_document(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        doc_id = kwargs.get("document_id", "")
        if not doc_id:
            return ToolResult(success=False, error="document_id is required")

        payload: dict[str, Any] = {}
        if kwargs.get("message"):
            payload["message"] = kwargs["message"]
        if kwargs.get("subject"):
            payload["subject"] = kwargs["subject"]

        resp = await client.post(f"{PANDADOC_BASE}/documents/{doc_id}/send", json=payload)
        resp.raise_for_status()
        log.info("pandadoc.document_sent", doc_id=doc_id)
        return ToolResult(
            success=True,
            output=f"Document {doc_id} sent successfully",
            data={"document_id": doc_id, "status": "sent"},
        )
