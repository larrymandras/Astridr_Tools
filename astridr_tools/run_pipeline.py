"""Run-pipeline tool — LLM-callable interface to the pipeline engine."""

from __future__ import annotations

from typing import Any

import structlog

from astridr.engine.pipelines import Pipeline, PipelineEngine, PipelineResult
from astridr.tools.base import BaseTool, ToolResult

logger = structlog.get_logger()


class RunPipelineTool(BaseTool):
    """Execute a named pipeline, optionally resuming from an approval gate.

    The LLM calls this tool to trigger declarative multi-step workflows.
    """

    name = "run_pipeline"
    description = (
        "Run a named pipeline (multi-step workflow). "
        "Optionally provide context variables or resume a halted pipeline."
    )
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pipeline_name": {
                "type": "string",
                "description": "Name of the pipeline to execute.",
            },
            "context": {
                "type": "object",
                "description": "Optional context variables for the pipeline (e.g. location, topic).",
                "additionalProperties": True,
            },
            "resume_token": {
                "type": "string",
                "description": "Resume token from a previously halted pipeline (approval gate).",
            },
            "approved": {
                "type": "boolean",
                "description": "Whether to approve a halted step (used with resume_token).",
            },
        },
        "required": ["pipeline_name"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        engine: PipelineEngine,
        pipelines: dict[str, Pipeline],
    ) -> None:
        self._engine = engine
        self._pipelines = pipelines

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute or resume a pipeline."""
        pipeline_name = kwargs.get("pipeline_name", "")
        context = kwargs.get("context") or {}
        resume_token = kwargs.get("resume_token")
        approved = kwargs.get("approved", True)

        # Resume flow
        if resume_token:
            try:
                result = await self._engine.resume(resume_token, approved=approved)
                return self._to_tool_result(result)
            except KeyError as exc:
                return ToolResult(success=False, error=str(exc))

        # Normal execution
        if not pipeline_name:
            return ToolResult(success=False, error="Missing required parameter: pipeline_name")

        pipeline = self._pipelines.get(pipeline_name)
        if pipeline is None:
            available = ", ".join(sorted(self._pipelines.keys())) or "(none)"
            return ToolResult(
                success=False,
                error=f"Unknown pipeline: {pipeline_name}. Available: {available}",
            )

        logger.info(
            "run_pipeline.starting",
            pipeline=pipeline_name,
            context_keys=list(context.keys()),
        )

        result = await self._engine.run(pipeline, context=context)
        return self._to_tool_result(result)

    @staticmethod
    def _to_tool_result(result: PipelineResult) -> ToolResult:
        """Convert a PipelineResult to a ToolResult."""
        data: dict[str, Any] = {
            "pipeline_name": result.pipeline_name,
            "step_results": result.step_results,
            "duration_ms": result.duration_ms,
        }

        if result.halted_at:
            data["halted_at"] = result.halted_at
            data["resume_token"] = result.resume_token
            return ToolResult(
                success=False,
                output=f"Pipeline halted at '{result.halted_at}' — approval required. "
                       f"Resume with token: {result.resume_token}",
                data=data,
            )

        if result.success:
            # Build a summary of step outputs
            summaries = []
            for step_name, step_result in result.step_results.items():
                summaries.append(f"- {step_name}: {step_result}")
            output = f"Pipeline '{result.pipeline_name}' completed.\n" + "\n".join(summaries)
            return ToolResult(success=True, output=output, data=data)

        return ToolResult(
            success=False,
            error=f"Pipeline '{result.pipeline_name}' failed.",
            data=data,
        )
