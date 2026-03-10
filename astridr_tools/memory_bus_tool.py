"""Memory bus tool — lets agents publish/query the shared knowledge store."""

from __future__ import annotations

from typing import Any

from astridr.memory.memory_bus import VALID_CATEGORIES, MemoryBus
from astridr.tools.base import BaseTool, ToolResult


def _format_entries(entries: list[dict[str, Any]]) -> str:
    """Format shared knowledge entries as readable lines with provenance."""
    lines: list[str] = []
    for entry in entries:
        ts = entry.get("published_at", "?")
        agent = entry.get("agent_id", "unknown")
        cat = entry.get("category", "")
        topic = entry.get("topic") or ""
        content = entry.get("content", "")
        sim = entry.get("similarity")
        prefix = f"- [{cat}] {ts} ({agent})"
        if topic:
            prefix += f" #{topic}"
        if sim is not None:
            prefix += f" [{sim:.2f}]"
        lines.append(f"{prefix}: {content}")
    return "\n".join(lines)


class MemoryBusTool(BaseTool):
    """Publish to or query the cross-agent shared knowledge store.

    Actions:
        publish — share a fact, decision, insight, event, or preference.
        query   — semantic search (or text fallback) across shared knowledge.
        recent  — browse recent entries in reverse chronological order.
    """

    name = "memory_bus"
    description = (
        "Cross-agent shared knowledge bus. Publish facts, decisions, insights, "
        "events, or preferences for all agents. Query by semantic search or browse recent entries."
    )
    approval_tier = "autonomous"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["publish", "query", "recent"],
                "description": "Operation: 'publish' to share knowledge, 'query' for semantic search, 'recent' to browse.",
            },
            "content": {
                "type": "string",
                "description": "For publish: the knowledge text. For query: the search text.",
            },
            "category": {
                "type": "string",
                "enum": sorted(VALID_CATEGORIES),
                "description": "Knowledge category.",
            },
            "topic": {
                "type": "string",
                "description": "Optional topic tag for filtering.",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Maximum entries to return (query/recent).",
            },
            "agent_filter": {
                "type": "string",
                "description": "Filter results to a specific agent ID.",
            },
            "metadata": {
                "type": "object",
                "description": "Arbitrary metadata to attach (publish only).",
            },
            "ttl_days": {
                "type": "integer",
                "description": "Auto-expire after N days (publish only). Omit for permanent.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, memory_bus: MemoryBus) -> None:
        self._bus = memory_bus

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Dispatch to publish, query, or recent."""
        agent_type_id = kwargs.pop("_agent_type_id", None) or "astridr"
        action = kwargs.get("action")

        if action == "publish":
            return await self._publish(agent_type_id, **kwargs)
        elif action == "query":
            return await self._query(**kwargs)
        elif action == "recent":
            return await self._recent(**kwargs)
        else:
            return ToolResult(
                success=False,
                error=f"Unknown action '{action}'. Use publish, query, or recent.",
            )

    async def _publish(self, agent_type_id: str, **kwargs: Any) -> ToolResult:
        content = kwargs.get("content", "").strip()
        if not content:
            return ToolResult(success=False, error="'content' is required for publish.")

        category = kwargs.get("category", "fact")
        if category not in VALID_CATEGORIES:
            return ToolResult(
                success=False,
                error=f"Invalid category '{category}'. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}",
            )

        await self._bus.publish(
            agent_id=agent_type_id,
            content=content,
            category=category,
            topic=kwargs.get("topic"),
            metadata=kwargs.get("metadata"),
            ttl_days=kwargs.get("ttl_days"),
        )
        return ToolResult(
            success=True,
            output=f"Published [{category}] to shared knowledge as {agent_type_id}.",
        )

    async def _query(self, **kwargs: Any) -> ToolResult:
        content = kwargs.get("content", "").strip()
        if not content:
            return ToolResult(success=False, error="'content' (search text) is required for query.")

        entries = await self._bus.query(
            query_text=content,
            limit=kwargs.get("limit", 10),
            category=kwargs.get("category"),
            agent_id=kwargs.get("agent_filter"),
            topic=kwargs.get("topic"),
        )

        if not entries:
            return ToolResult(success=True, output="No shared knowledge found.")

        return ToolResult(success=True, output=_format_entries(entries))

    async def _recent(self, **kwargs: Any) -> ToolResult:
        entries = await self._bus.recent(
            limit=kwargs.get("limit", 10),
            category=kwargs.get("category"),
            agent_id=kwargs.get("agent_filter"),
            topic=kwargs.get("topic"),
        )

        if not entries:
            return ToolResult(success=True, output="No recent shared knowledge.")

        return ToolResult(success=True, output=_format_entries(entries))
