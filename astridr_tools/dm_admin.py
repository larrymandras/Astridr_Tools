"""DM admin tool — manage DM pairing approvals.

Provides list_pending, approve, revoke, and list_approved actions
for the DM pairing security layer.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from astridr.security.dm_pairing import DMPairingLayer
from astridr.tools.base import BaseTool, ToolResult

logger = structlog.get_logger()


class DMAdminTool(BaseTool):
    """Manage DM pairing approvals for the security pipeline.

    Args:
        dm_layer: The active :class:`DMPairingLayer` instance.
    """

    name = "dm_admin"
    description = "Manage DM pairing approvals (list_pending, approve, revoke, list_approved)"
    approval_tier = "admin"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_pending", "approve", "revoke", "list_approved"],
            },
            "channel_id": {"type": "string"},
            "sender_id": {"type": "string"},
        },
        "required": ["action"],
    }

    def __init__(self, dm_layer: DMPairingLayer) -> None:
        self._dm_layer = dm_layer

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action", "")
        channel_id = kwargs.get("channel_id", "")
        sender_id = kwargs.get("sender_id", "")

        if action == "list_pending":
            pending = self._dm_layer.list_pending()
            return ToolResult(
                success=True,
                output=json.dumps(
                    [{"channel_id": c, "sender_id": s} for c, s in pending],
                    indent=2,
                ),
            )

        if action == "list_approved":
            approved = self._dm_layer.list_approved()
            return ToolResult(success=True, output=json.dumps(approved, indent=2))

        if action == "approve":
            if not channel_id or not sender_id:
                return ToolResult(
                    success=False,
                    error="channel_id and sender_id are required for approve",
                )
            await self._dm_layer.approve(channel_id, sender_id)
            return ToolResult(
                success=True,
                output=f"Approved {sender_id} on {channel_id}",
            )

        if action == "revoke":
            if not channel_id or not sender_id:
                return ToolResult(
                    success=False,
                    error="channel_id and sender_id are required for revoke",
                )
            await self._dm_layer.revoke(channel_id, sender_id)
            return ToolResult(
                success=True,
                output=f"Revoked {sender_id} on {channel_id}",
            )

        return ToolResult(success=False, error=f"Unknown action: {action}")
