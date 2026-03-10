"""Airtable integration tool — read and write Airtable bases and records.

Requires AIRTABLE_API_KEY environment variable (personal access token).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

AIRTABLE_BASE = "https://api.airtable.com/v0"


class AirtableTool(BaseTool):
    """Manage Airtable bases, tables, and records."""

    name = "airtable"
    description = "List bases, browse and manage records in Airtable"
    approval_tier = "supervised"  # has write actions

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_bases",
                    "list_records",
                    "get_record",
                    "create_record",
                    "update_record",
                    "search",
                ],
                "description": "The Airtable operation to perform.",
            },
            "base_id": {
                "type": "string",
                "description": "Airtable base ID (e.g. 'appXXXXXXXXXXXXXX').",
            },
            "table_name": {
                "type": "string",
                "description": "Table name or ID within the base.",
            },
            "record_id": {
                "type": "string",
                "description": "Record ID (for get_record / update_record).",
            },
            "fields": {
                "type": "object",
                "description": "Field values for create_record / update_record.",
            },
            "formula": {
                "type": "string",
                "description": "Airtable formula for filtering (filterByFormula).",
            },
            "query": {
                "type": "string",
                "description": "Search query (used to build a SEARCH formula).",
            },
            "search_field": {
                "type": "string",
                "description": "Field name to search within (for search action).",
            },
            "sort_field": {
                "type": "string",
                "description": "Field name to sort by.",
            },
            "sort_direction": {
                "type": "string",
                "enum": ["asc", "desc"],
                "description": "Sort direction.",
                "default": "asc",
            },
            "max_records": {
                "type": "integer",
                "description": "Maximum records to return.",
                "default": 100,
            },
            "view": {
                "type": "string",
                "description": "View name or ID to filter by.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    # Actions that only read data.
    _READ_ACTIONS: frozenset[str] = frozenset(
        {"list_bases", "list_records", "get_record", "search"}
    )

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("AIRTABLE_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        if not self.api_key:
            return ToolResult(success=False, error="AIRTABLE_API_KEY not configured")

        dispatch = {
            "list_bases": self._list_bases,
            "list_records": self._list_records,
            "get_record": self._get_record,
            "create_record": self._create_record,
            "update_record": self._update_record,
            "search": self._search,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("airtable.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Airtable API error {exc.response.status_code}: {exc.response.text}",
            )
        except httpx.HTTPError as exc:
            log.error("airtable.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def _list_bases(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        resp = await client.get("https://api.airtable.com/v0/meta/bases")
        resp.raise_for_status()
        data = resp.json()
        bases = data.get("bases", [])
        lines = [f"{b['id']} — {b.get('name', '')}" for b in bases]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"bases": bases},
        )

    async def _list_records(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        base_id = kwargs.get("base_id", "")
        table_name = kwargs.get("table_name", "")
        if not base_id or not table_name:
            return ToolResult(
                success=False, error="base_id and table_name are required for list_records"
            )

        params: dict[str, Any] = {
            "maxRecords": kwargs.get("max_records", 100),
        }
        if kwargs.get("formula"):
            params["filterByFormula"] = kwargs["formula"]
        if kwargs.get("view"):
            params["view"] = kwargs["view"]
        if kwargs.get("sort_field"):
            params["sort[0][field]"] = kwargs["sort_field"]
            params["sort[0][direction]"] = kwargs.get("sort_direction", "asc")

        resp = await client.get(f"{AIRTABLE_BASE}/{base_id}/{table_name}", params=params)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("records", [])
        lines = [f"{r['id']}: {r.get('fields', {})}" for r in records]
        return ToolResult(
            success=True,
            output="\n".join(lines[:50]),  # Cap output for readability
            data={"records": records, "offset": data.get("offset")},
        )

    async def _get_record(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        base_id = kwargs.get("base_id", "")
        table_name = kwargs.get("table_name", "")
        record_id = kwargs.get("record_id", "")
        if not base_id or not table_name or not record_id:
            return ToolResult(
                success=False,
                error="base_id, table_name, and record_id are required for get_record",
            )

        resp = await client.get(f"{AIRTABLE_BASE}/{base_id}/{table_name}/{record_id}")
        resp.raise_for_status()
        record = resp.json()
        return ToolResult(
            success=True,
            output=f"{record['id']}: {record.get('fields', {})}",
            data={"record": record},
        )

    async def _search(self, **kwargs: Any) -> ToolResult:
        """Search records using a SEARCH formula on a specified field."""
        client = self._ensure_client()
        base_id = kwargs.get("base_id", "")
        table_name = kwargs.get("table_name", "")
        query = kwargs.get("query", "")
        search_field = kwargs.get("search_field", "")
        if not base_id or not table_name or not query or not search_field:
            return ToolResult(
                success=False,
                error="base_id, table_name, query, and search_field are required for search",
            )

        # Build an Airtable SEARCH formula
        escaped_query = query.replace('"', '\\"')
        formula = f'SEARCH("{escaped_query}", {{{search_field}}})'

        params: dict[str, Any] = {
            "filterByFormula": formula,
            "maxRecords": kwargs.get("max_records", 100),
        }
        if kwargs.get("view"):
            params["view"] = kwargs["view"]

        resp = await client.get(f"{AIRTABLE_BASE}/{base_id}/{table_name}", params=params)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("records", [])
        lines = [f"{r['id']}: {r.get('fields', {})}" for r in records]
        return ToolResult(
            success=True,
            output="\n".join(lines[:50]),
            data={"records": records, "total": len(records)},
        )

    # ------------------------------------------------------------------
    # Write operations (supervised — require approval)
    # ------------------------------------------------------------------

    async def _create_record(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        base_id = kwargs.get("base_id", "")
        table_name = kwargs.get("table_name", "")
        fields = kwargs.get("fields")
        if not base_id or not table_name or not fields:
            return ToolResult(
                success=False,
                error="base_id, table_name, and fields are required for create_record",
            )

        payload = {"fields": fields}
        resp = await client.post(
            f"{AIRTABLE_BASE}/{base_id}/{table_name}",
            json=payload,
        )
        resp.raise_for_status()
        record = resp.json()
        log.info("airtable.record_created", base_id=base_id, table=table_name, record_id=record["id"])
        return ToolResult(
            success=True,
            output=f"Created record {record['id']}",
            data={"record": record},
        )

    async def _update_record(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        base_id = kwargs.get("base_id", "")
        table_name = kwargs.get("table_name", "")
        record_id = kwargs.get("record_id", "")
        fields = kwargs.get("fields")
        if not base_id or not table_name or not record_id or not fields:
            return ToolResult(
                success=False,
                error="base_id, table_name, record_id, and fields are required for update_record",
            )

        payload = {"fields": fields}
        resp = await client.patch(
            f"{AIRTABLE_BASE}/{base_id}/{table_name}/{record_id}",
            json=payload,
        )
        resp.raise_for_status()
        record = resp.json()
        log.info(
            "airtable.record_updated",
            base_id=base_id,
            table=table_name,
            record_id=record_id,
        )
        return ToolResult(
            success=True,
            output=f"Updated record {record_id}",
            data={"record": record},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_read_only(self, action: str) -> bool:
        """Return True if the given action is read-only (no approval needed)."""
        return action in self._READ_ACTIONS
