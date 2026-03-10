"""Base tool interface — all tools implement this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Result returned by a tool execution."""

    success: bool
    output: str = ""
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    """Abstract base class for tools.

    Every tool (shell, files, memory, web search, browser, etc.)
    implements this interface.
    """

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema

    # Approval tier: "read_only" | "supervised" | "autonomous"
    approval_tier: str = "supervised"

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with the given arguments.

        Args:
            **kwargs: Tool-specific arguments matching the parameters schema.

        Returns:
            ToolResult with success status and output.
        """
        ...

    def to_definition(self) -> dict[str, Any]:
        """Convert to the format expected by LLM providers."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
