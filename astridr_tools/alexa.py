"""Amazon Alexa integration tool — smart home and reminders.

OAuth only. Requires ALEXA_CLIENT_ID + ALEXA_CLIENT_SECRET (Login with Amazon).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult
from astridr.tools.oauth import OAuthTokenManager

log = structlog.get_logger()

ALEXA_API_BASE = "https://api.amazonalexa.com/v3"


class AlexaTool(BaseTool):
    """Control Alexa smart home devices, reminders, and routines."""

    name = "alexa"
    description = "Manage Alexa smart home — devices, commands, reminders, routines"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_devices",
                    "get_device_state",
                    "send_command",
                    "create_reminder",
                    "list_reminders",
                ],
                "description": "The Alexa operation to perform.",
            },
            "device_id": {
                "type": "string",
                "description": "Device endpoint ID.",
            },
            "command": {
                "type": "string",
                "enum": ["turn_on", "turn_off", "set_brightness", "set_color", "set_temperature", "lock", "unlock"],
                "description": "Command to send to a device.",
            },
            "value": {
                "type": "string",
                "description": "Value for the command (e.g. brightness '75', color '#ff0000', temp '72').",
            },
            "reminder_text": {
                "type": "string",
                "description": "Text for the reminder.",
            },
            "reminder_time": {
                "type": "string",
                "description": "Reminder time in ISO 8601 format.",
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
        {"list_devices", "get_device_state", "list_reminders"}
    )

    def __init__(self) -> None:
        self._oauth = OAuthTokenManager(
            provider="alexa",
            client_id_env="ALEXA_CLIENT_ID",
            client_secret_env="ALEXA_CLIENT_SECRET",
            token_url="https://api.amazon.com/auth/o2/token",
            scopes=["alexa::devices:all:devices:all:read", "alexa::devices:all:devices:all:write", "alexa::alerts:reminders:skill:readwrite"],
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
                error="Alexa OAuth not configured. Run: python -m astridr.tools.oauth_setup alexa",
            )

        dispatch = {
            "list_devices": self._list_devices,
            "get_device_state": self._get_device_state,
            "send_command": self._send_command,
            "create_reminder": self._create_reminder,
            "list_reminders": self._list_reminders,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("alexa.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Alexa API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("alexa.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _list_devices(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(f"{ALEXA_API_BASE}/endpoints", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        endpoints = data.get("endpoints", [])
        lines = [
            f"{e.get('endpointId', '')} — {e.get('friendlyName', '')} [{e.get('displayCategories', [''])[0]}]"
            for e in endpoints
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No devices found",
            data={"devices": endpoints},
        )

    async def _get_device_state(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        device_id = kwargs.get("device_id", "")
        if not device_id:
            return ToolResult(success=False, error="device_id is required")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(
            f"{ALEXA_API_BASE}/endpoints/{device_id}/state", headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        properties = data.get("properties", [])
        lines = [
            f"{p.get('namespace', '')}.{p.get('name', '')}: {p.get('value', '')}"
            for p in properties
        ]
        return ToolResult(
            success=True,
            output=f"Device {device_id}:\n" + "\n".join(lines) if lines else f"No state for {device_id}",
            data={"device_id": device_id, "properties": properties},
        )

    async def _send_command(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        device_id = kwargs.get("device_id", "")
        command = kwargs.get("command", "")
        if not device_id or not command:
            return ToolResult(
                success=False, error="device_id and command are required for send_command"
            )

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        directive: dict[str, Any] = {
            "directive": {
                "endpoint": {"endpointId": device_id},
                "payload": {},
            }
        }

        command_map = {
            "turn_on": ("Alexa.PowerController", "TurnOn"),
            "turn_off": ("Alexa.PowerController", "TurnOff"),
            "set_brightness": ("Alexa.BrightnessController", "SetBrightness"),
            "set_color": ("Alexa.ColorController", "SetColor"),
            "set_temperature": ("Alexa.ThermostatController", "SetTargetTemperature"),
            "lock": ("Alexa.LockController", "Lock"),
            "unlock": ("Alexa.LockController", "Unlock"),
        }

        if command not in command_map:
            return ToolResult(success=False, error=f"Unknown command: {command}")

        namespace, name = command_map[command]
        directive["directive"]["header"] = {"namespace": namespace, "name": name}

        value = kwargs.get("value", "")
        if command == "set_brightness" and value:
            directive["directive"]["payload"]["brightness"] = int(value)
        elif command == "set_color" and value:
            directive["directive"]["payload"]["color"] = {"value": value}
        elif command == "set_temperature" and value:
            directive["directive"]["payload"]["targetSetpoint"] = {
                "value": float(value),
                "scale": "FAHRENHEIT",
            }

        resp = await client.post(
            f"{ALEXA_API_BASE}/directives",
            json=directive,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        log.info("alexa.command_sent", device_id=device_id, command=command)
        return ToolResult(
            success=True,
            output=f"Command '{command}' sent to device {device_id}",
            data={"device_id": device_id, "command": command},
        )

    async def _create_reminder(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        reminder_text = kwargs.get("reminder_text", "")
        reminder_time = kwargs.get("reminder_time", "")
        if not reminder_text or not reminder_time:
            return ToolResult(
                success=False, error="reminder_text and reminder_time are required"
            )

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        payload = {
            "requestTime": reminder_time,
            "trigger": {"type": "SCHEDULED_ABSOLUTE", "scheduledTime": reminder_time},
            "alertInfo": {
                "spokenInfo": {
                    "content": [{"locale": "en-US", "text": reminder_text}]
                }
            },
            "pushNotification": {"status": "ENABLED"},
        }

        resp = await client.post(
            f"{ALEXA_API_BASE}/alerts/reminders",
            json=payload,
            headers={**headers, "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        alert_id = data.get("alertToken", "")
        log.info("alexa.reminder_created", alert_id=alert_id)
        return ToolResult(
            success=True,
            output=f"Reminder created: {alert_id}\n{reminder_text} at {reminder_time}",
            data={"alert_id": alert_id, "text": reminder_text, "time": reminder_time},
        )

    async def _list_reminders(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(f"{ALEXA_API_BASE}/alerts/reminders", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        reminders = data.get("alerts", [])
        lines = [
            f"{r.get('alertToken', '')} — {r.get('status', '')} — "
            f"{r.get('alertInfo', {}).get('spokenInfo', {}).get('content', [{}])[0].get('text', '')}"
            for r in reminders
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No reminders found",
            data={"reminders": reminders},
        )
