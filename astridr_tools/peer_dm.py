"""Peer DM tool — lets agents send direct messages to other agents."""

from __future__ import annotations

from typing import Any

from astridr.tools.base import BaseTool, ToolResult


class PeerDMTool(BaseTool):
    """Send a direct message to another agent via the coordinator.

    Wraps :meth:`AgentCoordinator.route_message` with peer-comm
    permission checks from the agent type registry.

    Args:
        coordinator: The AgentCoordinator that routes messages.
        agent_type_registry: Registry for looking up peer_comm_allowed.
    """

    name = "send_dm"
    description = (
        "Send a direct message to another agent. "
        "Specify the target agent and message content."
    )
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "to_agent": {
                "type": "string",
                "description": "ID of the agent to send the message to.",
            },
            "message": {
                "type": "string",
                "description": "Message content to send.",
            },
        },
        "required": ["to_agent", "message"],
        "additionalProperties": False,
    }

    def __init__(self, coordinator: Any, agent_type_registry: Any) -> None:
        self._coordinator = coordinator
        self._registry = agent_type_registry

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Route a message to a peer agent."""
        to_agent = kwargs.get("to_agent", "").strip()
        message = kwargs.get("message", "").strip()

        if not to_agent:
            return ToolResult(success=False, error="Missing required parameter: to_agent")
        if not message:
            return ToolResult(success=False, error="Missing required parameter: message")

        # Determine the calling agent's identity and allowed peers.
        # Sub-agents get their own identity via agent_type_id; the
        # commander (default) has no peer restrictions.
        from_agent = kwargs.get("_agent_type_id", "astridr")
        allowed_peers: list[str] | None = None

        if from_agent != "astridr":
            agent_type = self._registry.get(from_agent)
            if agent_type is not None:
                allowed_peers = getattr(agent_type, "peer_comm_allowed", None)

        try:
            response_text = await self._coordinator.route_message(
                from_agent=from_agent,
                to_agent=to_agent,
                message=message,
                allowed_peers=allowed_peers,
            )
            return ToolResult(
                success=True,
                output=f"[Response from {to_agent}]: {response_text}",
                data={"from_agent": from_agent, "to_agent": to_agent},
            )
        except PermissionError as exc:
            return ToolResult(success=False, error=str(exc))
