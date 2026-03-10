"""Episodic recall tool — lets agents introspect their own event history."""

from __future__ import annotations

from typing import Any

from astridr.memory.episodic import EpisodicMemory
from astridr.tools.base import BaseTool, ToolResult


def _format_events(events: list[dict[str, Any]]) -> str:
    """Format episodic events as human-readable lines."""
    lines: list[str] = []
    for ev in events:
        ts = ev.get("occurred_at", "?")
        etype = ev.get("event_type", "unknown")
        summary = ev.get("summary", "")
        lines.append(f"- [{etype}] {ts}: {summary}")
    return "\n".join(lines)


class EpisodicRecallTool(BaseTool):
    """Recall or clean up episodic memories (90-day rolling event store).

    Actions:
        recall  — retrieve recent events, optionally filtered by type or query.
        cleanup — delete expired episodes and return the count removed.
    """

    name = "episodic_recall"
    description = (
        "Recall recent episodic events or clean up expired entries. "
        "Use action 'recall' with optional event_type/query, or 'cleanup' to purge stale data."
    )
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["recall", "cleanup"],
                "description": "Operation: 'recall' to retrieve events, 'cleanup' to purge expired.",
            },
            "event_type": {
                "type": "string",
                "description": "Filter by event type (e.g. 'tool_call', 'session_start').",
            },
            "query": {
                "type": "string",
                "description": "Search term to match against event summaries.",
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "Maximum number of events to return.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, episodic: EpisodicMemory) -> None:
        self._episodic = episodic

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Dispatch to recall or cleanup."""
        agent_type_id = kwargs.pop("_agent_type_id", None) or "astridr"

        action = kwargs.get("action", "recall")

        if action == "cleanup":
            count = await self._episodic.cleanup_expired()
            return ToolResult(success=True, output=f"Cleaned up {count} expired episode(s).")

        # Default: recall
        event_type = kwargs.get("event_type")
        query = kwargs.get("query")
        limit = kwargs.get("limit", 20)

        events = await self._episodic.recall(
            agent_id=agent_type_id,
            event_type=event_type,
            query=query,
            limit=limit,
        )

        if not events:
            return ToolResult(success=True, output="No episodic events found.")

        return ToolResult(success=True, output=_format_events(events))
