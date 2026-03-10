"""Memory save tool — lets agents persist memories autonomously."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from astridr.memory.base import BaseMemory
from astridr.memory.agent_router import AgentMemoryRouter
from astridr.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from astridr.memory.obsidian_sync import ObsidianMemorySync

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = frozenset({"fact", "preference", "decision", "solution", "log"})


class MemorySaveTool(BaseTool):
    """Save a memory entry to the agent's memory store.

    Wraps :meth:`BaseMemory.save` so that agents can persist
    facts, preferences, decisions, solutions, and log entries.

    Args:
        memory_store: The memory backend to write to.
    """

    name = "memory_save"
    description = (
        "Save a memory entry. Use this to remember facts, preferences, "
        "decisions, solutions, or log entries for future reference."
    )
    approval_tier = "autonomous"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The memory content to save.",
            },
            "category": {
                "type": "string",
                "enum": ["fact", "preference", "decision", "solution", "log"],
                "description": "Memory category. Defaults to 'fact'.",
            },
            "topic": {
                "type": "string",
                "description": "Optional topic grouping for the memory.",
            },
        },
        "required": ["content"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        memory_store: BaseMemory | AgentMemoryRouter,
        sync_resolver: Callable[[str | None], ObsidianMemorySync | None] | None = None,
    ) -> None:
        self._store = memory_store
        self._sync_resolver = sync_resolver

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Save a memory entry and return the file path."""
        agent_type_id = kwargs.pop("_agent_type_id", None)

        store = self._store
        if isinstance(store, AgentMemoryRouter):
            store = store.get_store(agent_type_id)

        content = kwargs.get("content", "").strip()
        if not content:
            return ToolResult(success=False, error="Missing required parameter: content")

        category = kwargs.get("category", "fact")
        if category not in _VALID_CATEGORIES:
            return ToolResult(
                success=False,
                error=f"Invalid category '{category}'. Must be one of: {sorted(_VALID_CATEGORIES)}",
            )

        topic = kwargs.get("topic") or None

        path = await store.save(content, category=category, topic=topic)

        # Push to Obsidian vault (non-blocking — sync failures never block save)
        if self._sync_resolver is not None:
            try:
                sync = self._sync_resolver(agent_type_id)
                if sync:
                    from astridr.memory.store import _parse_memory_file

                    entry = _parse_memory_file(Path(path))
                    if entry:
                        await sync.push_memory(entry)
            except Exception as exc:
                logger.warning("memory_save.obsidian_push_failed: %s", exc)

        return ToolResult(success=True, output=f"Memory saved to {path}")
