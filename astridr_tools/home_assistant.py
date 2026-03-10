"""Home Assistant integration tool — smart home control.

Requires HOME_ASSISTANT_TOKEN (long-lived access token) and HOME_ASSISTANT_URL.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class HomeAssistantTool(BaseTool):
    """Control Home Assistant: entities, services, automations."""

    name = "home_assistant"
    description = "Control Home Assistant smart home — entities, services, automations"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_entities",
                    "get_entity_state",
                    "call_service",
                    "list_automations",
                    "trigger_automation",
                ],
                "description": "The Home Assistant operation to perform.",
            },
            "entity_id": {
                "type": "string",
                "description": "Entity ID (e.g. 'light.living_room', 'switch.kitchen').",
            },
            "domain": {
                "type": "string",
                "description": "Service domain (e.g. 'light', 'switch', 'climate', 'cover').",
            },
            "service": {
                "type": "string",
                "description": "Service name (e.g. 'turn_on', 'turn_off', 'toggle').",
            },
            "service_data": {
                "type": "object",
                "description": "Additional data for service call (e.g. brightness, temperature).",
            },
            "automation_id": {
                "type": "string",
                "description": "Automation entity ID (e.g. 'automation.morning_lights').",
            },
            "entity_type": {
                "type": "string",
                "description": "Filter entities by type (e.g. 'light', 'switch', 'sensor').",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset(
        {"list_entities", "get_entity_state", "list_automations"}
    )

    def __init__(self) -> None:
        self._token = os.environ.get("HOME_ASSISTANT_TOKEN", "")
        self._base_url = os.environ.get("HOME_ASSISTANT_URL", "http://homeassistant.local:8123")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
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
        if not self._token:
            return ToolResult(success=False, error="HOME_ASSISTANT_TOKEN not configured")

        dispatch = {
            "list_entities": self._list_entities,
            "get_entity_state": self._get_entity_state,
            "call_service": self._call_service,
            "list_automations": self._list_automations,
            "trigger_automation": self._trigger_automation,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("home_assistant.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Home Assistant API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("home_assistant.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _list_entities(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        resp = await client.get(f"{self._base_url}/api/states")
        resp.raise_for_status()
        entities = resp.json()

        entity_type = kwargs.get("entity_type", "")
        if entity_type:
            entities = [e for e in entities if e.get("entity_id", "").startswith(f"{entity_type}.")]

        lines = [
            f"{e.get('entity_id', '')} — {e.get('state', '')} "
            f"({e.get('attributes', {}).get('friendly_name', '')})"
            for e in entities
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No entities found",
            data={"entities": entities, "count": len(entities)},
        )

    async def _get_entity_state(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        entity_id = kwargs.get("entity_id", "")
        if not entity_id:
            return ToolResult(success=False, error="entity_id is required")

        resp = await client.get(f"{self._base_url}/api/states/{entity_id}")
        resp.raise_for_status()
        data = resp.json()
        state = data.get("state", "unknown")
        attrs = data.get("attributes", {})
        friendly = attrs.get("friendly_name", entity_id)
        last_changed = data.get("last_changed", "")

        attr_lines = [f"  {k}: {v}" for k, v in attrs.items() if k != "friendly_name"]
        output = (
            f"{friendly} ({entity_id})\n"
            f"State: {state}\n"
            f"Last changed: {last_changed}\n"
            f"Attributes:\n" + "\n".join(attr_lines)
        )
        return ToolResult(success=True, output=output, data={"entity": data})

    async def _call_service(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        domain = kwargs.get("domain", "")
        service = kwargs.get("service", "")
        if not domain or not service:
            return ToolResult(
                success=False, error="domain and service are required for call_service"
            )

        payload: dict[str, Any] = {}
        if kwargs.get("entity_id"):
            payload["entity_id"] = kwargs["entity_id"]
        if kwargs.get("service_data"):
            payload.update(kwargs["service_data"])

        resp = await client.post(
            f"{self._base_url}/api/services/{domain}/{service}", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        entity_id = kwargs.get("entity_id", "")
        log.info("home_assistant.service_called", domain=domain, service=service, entity=entity_id)
        return ToolResult(
            success=True,
            output=f"Service {domain}.{service} called"
            + (f" on {entity_id}" if entity_id else ""),
            data={"result": data, "domain": domain, "service": service},
        )

    async def _list_automations(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        resp = await client.get(f"{self._base_url}/api/states")
        resp.raise_for_status()
        all_entities = resp.json()
        automations = [
            e for e in all_entities if e.get("entity_id", "").startswith("automation.")
        ]
        lines = [
            f"{a.get('entity_id', '')} — {a.get('state', '')} "
            f"({a.get('attributes', {}).get('friendly_name', '')})"
            for a in automations
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No automations found",
            data={"automations": automations, "count": len(automations)},
        )

    async def _trigger_automation(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        automation_id = kwargs.get("automation_id", "")
        if not automation_id:
            return ToolResult(success=False, error="automation_id is required")

        resp = await client.post(
            f"{self._base_url}/api/services/automation/trigger",
            json={"entity_id": automation_id},
        )
        resp.raise_for_status()
        log.info("home_assistant.automation_triggered", automation_id=automation_id)
        return ToolResult(
            success=True,
            output=f"Automation triggered: {automation_id}",
            data={"automation_id": automation_id, "triggered": True},
        )
