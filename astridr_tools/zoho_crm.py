"""Zoho CRM integration tool — manage CRM records, modules, and search.

OAuth only. Requires ZOHO_CLIENT_ID + ZOHO_CLIENT_SECRET.
Optional: ZOHO_DOMAIN (default 'com').
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult
from astridr.tools.oauth import OAuthTokenManager

log = structlog.get_logger()


class ZohoCRMTool(BaseTool):
    """Manage Zoho CRM: records, modules, search."""

    name = "zoho_crm"
    description = "Manage Zoho CRM records, modules, and search"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_records",
                    "get_record",
                    "create_record",
                    "update_record",
                    "search",
                    "list_modules",
                ],
                "description": "The Zoho CRM operation to perform.",
            },
            "module": {
                "type": "string",
                "description": "CRM module name (e.g. 'Contacts', 'Leads', 'Deals', 'Accounts').",
            },
            "record_id": {
                "type": "string",
                "description": "Record ID for get/update operations.",
            },
            "fields": {
                "type": "object",
                "description": "Field values for create/update.",
            },
            "criteria": {
                "type": "string",
                "description": "Search criteria (e.g. '(Last_Name:equals:Smith)').",
            },
            "query": {
                "type": "string",
                "description": "Search word for keyword search.",
            },
            "sort_by": {
                "type": "string",
                "description": "Field to sort by.",
            },
            "sort_order": {
                "type": "string",
                "enum": ["asc", "desc"],
                "description": "Sort order.",
                "default": "desc",
            },
            "page": {
                "type": "integer",
                "description": "Page number for pagination.",
                "default": 1,
            },
            "per_page": {
                "type": "integer",
                "description": "Records per page (max 200).",
                "default": 50,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset(
        {"list_records", "get_record", "search", "list_modules"}
    )

    def __init__(self) -> None:
        self._domain = os.environ.get("ZOHO_DOMAIN", "com")
        self._oauth = OAuthTokenManager(
            provider="zoho",
            client_id_env="ZOHO_CLIENT_ID",
            client_secret_env="ZOHO_CLIENT_SECRET",
            token_url=f"https://accounts.zoho.{self._domain}/oauth/v2/token",
            scopes=["ZohoCRM.modules.ALL", "ZohoCRM.settings.ALL"],
        )
        self._client: httpx.AsyncClient | None = None

    @property
    def _base_url(self) -> str:
        return f"https://www.zohoapis.{self._domain}/crm/v5"

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
        return {"Authorization": f"Zoho-oauthtoken {token}"}

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        if not await self._oauth.is_authenticated():
            return ToolResult(
                success=False,
                error="Zoho CRM OAuth not configured. Run: python -m astridr.tools.oauth_setup zoho",
            )

        dispatch = {
            "list_records": self._list_records,
            "get_record": self._get_record,
            "create_record": self._create_record,
            "update_record": self._update_record,
            "search": self._search,
            "list_modules": self._list_modules,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("zoho_crm.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Zoho CRM API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("zoho_crm.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _list_records(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        module = kwargs.get("module", "")
        if not module:
            return ToolResult(success=False, error="module is required for list_records")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        params: dict[str, Any] = {
            "page": kwargs.get("page", 1),
            "per_page": min(kwargs.get("per_page", 50), 200),
        }
        if kwargs.get("sort_by"):
            params["sort_by"] = kwargs["sort_by"]
            params["sort_order"] = kwargs.get("sort_order", "desc")

        resp = await client.get(
            f"{self._base_url}/{module}",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        records = data.get("data", [])
        info = data.get("info", {})
        lines = []
        for r in records:
            name = r.get("Full_Name") or r.get("Deal_Name") or r.get("Account_Name") or r.get("id", "")
            lines.append(f"{r.get('id', '')} — {name}")

        return ToolResult(
            success=True,
            output=f"{module} ({info.get('count', len(records))} records):\n" + "\n".join(lines),
            data={"records": records, "info": info},
        )

    async def _get_record(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        module = kwargs.get("module", "")
        record_id = kwargs.get("record_id", "")
        if not module or not record_id:
            return ToolResult(success=False, error="module and record_id are required")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(
            f"{self._base_url}/{module}/{record_id}",
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        records = data.get("data", [])
        if not records:
            return ToolResult(success=False, error=f"Record {record_id} not found in {module}")

        record = records[0]
        output = f"Record {record_id} in {module}:\n"
        for key, value in record.items():
            if value is not None and key != "id":
                output += f"  {key}: {value}\n"
        return ToolResult(success=True, output=output[:2000], data={"record": record})

    async def _create_record(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        module = kwargs.get("module", "")
        fields = kwargs.get("fields")
        if not module or not fields:
            return ToolResult(success=False, error="module and fields are required")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        payload = {"data": [fields]}
        resp = await client.post(
            f"{self._base_url}/{module}",
            json=payload,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        created = data.get("data", [{}])[0]
        record_id = created.get("details", {}).get("id", "")
        status = created.get("status", "")
        log.info("zoho_crm.record_created", module=module, record_id=record_id)
        return ToolResult(
            success=True,
            output=f"Created record in {module}: {record_id} (status: {status})",
            data={"record": created, "record_id": record_id},
        )

    async def _update_record(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        module = kwargs.get("module", "")
        record_id = kwargs.get("record_id", "")
        fields = kwargs.get("fields")
        if not module or not record_id or not fields:
            return ToolResult(success=False, error="module, record_id, and fields are required")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        payload = {"data": [{"id": record_id, **fields}]}
        resp = await client.put(
            f"{self._base_url}/{module}/{record_id}",
            json=payload,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        updated = data.get("data", [{}])[0]
        log.info("zoho_crm.record_updated", module=module, record_id=record_id)
        return ToolResult(
            success=True,
            output=f"Updated record {record_id} in {module}",
            data={"record": updated},
        )

    async def _search(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        module = kwargs.get("module", "")
        if not module:
            return ToolResult(success=False, error="module is required for search")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        params: dict[str, Any] = {
            "page": kwargs.get("page", 1),
            "per_page": min(kwargs.get("per_page", 50), 200),
        }
        if kwargs.get("criteria"):
            params["criteria"] = kwargs["criteria"]
        elif kwargs.get("query"):
            params["word"] = kwargs["query"]
        else:
            return ToolResult(success=False, error="criteria or query is required for search")

        resp = await client.get(
            f"{self._base_url}/{module}/search",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        records = data.get("data", [])
        lines = []
        for r in records:
            name = r.get("Full_Name") or r.get("Deal_Name") or r.get("Account_Name") or r.get("id", "")
            lines.append(f"{r.get('id', '')} — {name}")

        return ToolResult(
            success=True,
            output=f"Search results in {module} ({len(records)}):\n" + "\n".join(lines),
            data={"records": records},
        )

    async def _list_modules(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(
            f"{self._base_url}/settings/modules",
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        modules = data.get("modules", [])
        lines = [
            f"{m.get('api_name', '')} — {m.get('plural_label', '')} [{'editable' if m.get('editable') else 'read-only'}]"
            for m in modules
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"modules": modules},
        )
