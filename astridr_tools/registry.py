"""Central tool registry — register, look up, and list tools."""

from __future__ import annotations

import fnmatch
from typing import Any

import structlog

from astridr.engine.config import ProfileConfig
from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class ToolRegistry:
    """Registry for all tools (built-in, skills, MCP).

    Provides registration, lookup by name, and profile-aware filtering
    so that different profiles can enable/disable specific tools.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: BaseTool) -> None:
        """Register a tool.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool
        log.info("tool.registered", name=tool.name)

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry.

        Raises:
            KeyError: If no tool with the given name exists.
        """
        if name not in self._tools:
            raise KeyError(f"Tool not registered: {name}")
        del self._tools[name]
        log.info("tool.unregistered", name=name)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> BaseTool | None:
        """Look up a tool by name. Returns None if not found."""
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # ------------------------------------------------------------------
    # Listing / filtering
    # ------------------------------------------------------------------

    def list_tools(self, profile: ProfileConfig | None = None) -> list[BaseTool]:
        """List all registered tools, optionally filtered by profile permissions.

        Profile filtering rules:
        - ``tools_enabled`` uses glob patterns. ``["*"]`` means all tools.
        - ``tools_disabled`` patterns are then subtracted.
        - A tool must match at least one enabled pattern and none of the
          disabled patterns to be included.
        """
        all_tools = list(self._tools.values())
        if profile is None:
            return all_tools
        return [t for t in all_tools if self._profile_allows(t.name, profile)]

    def list_tool_names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def get_definitions(self, profile: ProfileConfig | None = None) -> list[dict[str, Any]]:
        """Get tool definitions for the LLM, optionally filtered by profile."""
        return [t.to_definition() for t in self.list_tools(profile)]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _profile_allows(tool_name: str, profile: ProfileConfig) -> bool:
        """Return True if *profile* permits the tool *tool_name*."""
        # Check enabled list (glob patterns)
        enabled = any(fnmatch.fnmatch(tool_name, pat) for pat in profile.tools_enabled)
        if not enabled:
            return False
        # Check disabled list (glob patterns take precedence)
        disabled = any(fnmatch.fnmatch(tool_name, pat) for pat in profile.tools_disabled)
        return not disabled


# ---------------------------------------------------------------------------
# Input validation helper (PYDAI-01)
# ---------------------------------------------------------------------------

def validate_tool_input(tool_name: str, kwargs: dict[str, Any]) -> dict[str, Any] | ToolResult:
    """Validate tool input kwargs against the registered Pydantic contract.

    Returns the validated (and coerced) kwargs dict on success.
    Returns a ToolResult(success=False) if validation fails.
    Returns kwargs unchanged if no contract is registered for tool_name.

    Designed to be called by the tool executor before tool.execute(**kwargs).
    Raises no exceptions — validation failures are returned as ToolResult errors
    so the agent loop can surface them cleanly.

    Args:
        tool_name: Name of the tool whose contract to validate against.
        kwargs: Raw keyword arguments from the LLM tool call.

    Returns:
        Validated kwargs dict, or ToolResult with error on validation failure.
    """
    from astridr.agent.tool_contracts import get_contract  # local import avoids circular

    contract = get_contract(tool_name)
    if contract is None:
        return kwargs

    try:
        validated = contract.model_validate(kwargs)
        return validated.model_dump()
    except Exception as exc:
        log.warning(
            "tool_registry.input_validation_failed",
            tool=tool_name,
            error=str(exc),
        )
        return ToolResult(success=False, error=f"Input validation failed for '{tool_name}': {exc}")
