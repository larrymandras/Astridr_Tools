"""Delegate-task tool — hand off work to a specialist sub-agent."""

from __future__ import annotations

from typing import Any

import structlog

from astridr.automation.subagents import SubAgentConfig, SubAgentManager
from astridr.tools.base import BaseTool, ToolResult

if __name__ != "__main__":
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from astridr.automation.agent_types import AgentTypeRegistry

logger = structlog.get_logger()


class DelegateTaskTool(BaseTool):
    """Delegate a task to a specialist sub-agent by type.

    The main agent calls this tool to spawn a typed sub-agent
    (e.g. researcher, coder, analyst) that runs autonomously
    and returns its output.

    Args:
        sub_agent_manager: The SubAgentManager that handles spawning.
    """

    name = "delegate_task"
    description = (
        "Delegate a task to a specialist sub-agent. "
        "Specify the agent type and a clear task description."
    )
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "agent_type_id": {
                "type": "string",
                "description": (
                    "ID of the agent type to delegate to "
                    "(e.g. 'hervor', 'freya', 'brynhildr', 'ragnhildr', "
                    "'gondul', 'skuld', 'hildr', 'idunn', 'urdhr')."
                ),
            },
            "task": {
                "type": "string",
                "description": "Clear description of the task to delegate.",
            },
            "context": {
                "type": "string",
                "description": (
                    "Optional supporting context for the receiving agent "
                    "(background info, reference data, constraints)."
                ),
            },
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "high", "critical"],
                "description": "Task priority level. Defaults to 'normal'.",
            },
        },
        "required": ["agent_type_id", "task"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        sub_agent_manager: SubAgentManager,
        agent_type_registry: AgentTypeRegistry | None = None,
    ) -> None:
        self._manager = sub_agent_manager
        self._registry = agent_type_registry

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Spawn a sub-agent for the given task and return its output."""
        agent_type_id = kwargs.get("agent_type_id", "")
        task = kwargs.get("task", "")
        context = kwargs.get("context", "")
        priority = kwargs.get("priority", "normal")
        calling_agent_id = kwargs.get("_agent_type_id", "")

        if not agent_type_id:
            return ToolResult(
                success=False,
                error="Missing required parameter: agent_type_id",
            )
        if not task:
            return ToolResult(success=False, error="Missing required parameter: task")

        # Prepend context to the task description if provided
        full_task = task
        if context:
            full_task = f"Context: {context}\n\nTask: {task}"

        # Resolve profile_id from the target agent type's config
        profile_id = "default"
        if self._registry:
            agent_type = self._registry.get(agent_type_id)
            if agent_type and agent_type.profiles:
                profile_id = agent_type.profiles[0]

        config = SubAgentConfig(
            task=full_task,
            profile_id=profile_id,
            agent_type_id=agent_type_id,
            parent_agent_id=calling_agent_id,
            parent_context=context,
        )

        logger.info(
            "delegate_task.spawning",
            agent_type_id=agent_type_id,
            task=task[:80],
            priority=priority,
        )

        result = await self._manager.spawn(config)

        if result.success:
            return ToolResult(
                success=True,
                output=result.output,
                data={
                    "agent_id": result.agent_id,
                    "agent_type_id": agent_type_id,
                    "duration_ms": result.duration_ms,
                },
            )

        return ToolResult(
            success=False,
            error=result.error or "Sub-agent execution failed",
            data={"agent_id": result.agent_id, "agent_type_id": agent_type_id},
        )
