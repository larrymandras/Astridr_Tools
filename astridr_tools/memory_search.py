"""Memory search tool — lets agents query their memory store."""

from __future__ import annotations

from typing import Any

from astridr.memory.base import BaseMemory, MemoryEntry
from astridr.memory.agent_router import AgentMemoryRouter
from astridr.tools.base import BaseTool, ToolResult


def _format_entries(entries: list[MemoryEntry]) -> str:
    """Format memory entries as human-readable lines."""
    lines: list[str] = []
    for entry in entries:
        preview = entry.content[:120]
        if len(entry.content) > 120:
            preview += "..."
        score_str = f" (score: {entry.score:.2f})" if entry.score else ""
        lines.append(f"- [{entry.category}] {preview}{score_str}")
    return "\n".join(lines)


class MemorySearchTool(BaseTool):
    """Search or browse the agent's memory store.

    Wraps :meth:`BaseMemory.search` and :meth:`BaseMemory.recent`
    so agents can recall previously saved memories.

    Args:
        memory_store: The memory backend to query.
    """

    name = "memory_search"
    description = (
        "Search memories or retrieve recent entries. "
        "Use action 'search' with a query, or 'recent' to list latest memories."
    )
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Maximum number of results to return.",
            },
            "action": {
                "type": "string",
                "enum": ["search", "recent"],
                "description": "Operation: 'search' for query-based lookup, 'recent' for latest entries.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, memory_store: BaseMemory | AgentMemoryRouter) -> None:
        self._store = memory_store

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Dispatch to search or recent based on action."""
        agent_type_id = kwargs.pop("_agent_type_id", None)

        store = self._store
        if isinstance(store, AgentMemoryRouter):
            store = store.get_store(agent_type_id)

        action = kwargs.get("action", "search")
        query = kwargs.get("query", "").strip()
        limit = kwargs.get("limit", 10)

        if action == "recent":
            entries = await store.recent(limit=limit)
        else:
            if not query:
                return ToolResult(
                    success=False,
                    error="Missing required parameter: query",
                )
            entries = await store.search(query, limit=limit)

        if not entries:
            return ToolResult(success=True, output="No memories found.")

        return ToolResult(success=True, output=_format_entries(entries))
