"""Coding agent orchestration — execute coding tasks as sub-agent work.

Provides a tool that reads files, validates paths, and formats structured
task prompts for a parent agent to execute coding operations like writing
code, refactoring, debugging, and testing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class CodingAgentTool(BaseTool):
    """Orchestrate coding tasks as sub-agent work."""

    name = "code_agent"
    description = "Execute coding tasks: write code, refactor, debug, test"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "What the coding agent should do.",
            },
            "working_dir": {
                "type": "string",
                "description": "Working directory path.",
            },
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Files to include in context.",
            },
            "language": {
                "type": "string",
                "description": "Programming language hint.",
            },
            "timeout": {
                "type": "integer",
                "description": "Max execution time in seconds.",
                "default": 300,
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    }

    # Maximum file size to read (10MB)
    MAX_FILE_SIZE = 10 * 1024 * 1024

    def __init__(self, allowed_dirs: list[Path] | None = None) -> None:
        self._allowed_dirs = allowed_dirs or []

    # ------------------------------------------------------------------
    # BaseTool.execute
    # ------------------------------------------------------------------

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Validate inputs, read files, and format task for the coding agent."""
        task: str = kwargs.get("task", "")
        if not task:
            return ToolResult(success=False, error="Missing required parameter: task")

        working_dir_str: str = kwargs.get("working_dir", "")
        files: list[str] = kwargs.get("files", [])
        language: str | None = kwargs.get("language")
        timeout: int = kwargs.get("timeout", 300)

        # Validate working directory if provided
        working_dir: Path | None = None
        if working_dir_str:
            try:
                working_dir = self._validate_working_dir(working_dir_str)
            except ValueError as exc:
                return ToolResult(success=False, error=str(exc))

        # Read files for context
        file_contents: dict[str, str] = {}
        if files:
            try:
                file_contents = await self._read_files(files, working_dir)
            except ValueError as exc:
                return ToolResult(success=False, error=str(exc))

        # Format the task prompt
        formatted = self._format_task(task, file_contents, language)

        log.info(
            "code_agent.task_prepared",
            task_length=len(task),
            files_count=len(file_contents),
            language=language,
            timeout=timeout,
        )

        return ToolResult(
            success=True,
            output=formatted,
            data={
                "task": task,
                "working_dir": str(working_dir) if working_dir else "",
                "files_read": list(file_contents.keys()),
                "language": language or "",
                "timeout": timeout,
            },
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_working_dir(self, path: str) -> Path:
        """Validate that the working directory is allowed.

        Args:
            path: Working directory path string.

        Returns:
            Resolved Path object.

        Raises:
            ValueError: If the path is not in the allowed directories list.
        """
        resolved = Path(path).resolve()

        if not resolved.is_dir():
            raise ValueError(f"Working directory does not exist: {path}")

        if self._allowed_dirs:
            allowed = any(
                self._is_subpath(resolved, allowed_dir.resolve())
                for allowed_dir in self._allowed_dirs
            )
            if not allowed:
                raise ValueError(
                    f"Working directory not in allowed list: {path}"
                )

        return resolved

    @staticmethod
    def _is_subpath(path: Path, parent: Path) -> bool:
        """Check if path is equal to or a child of parent."""
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    async def _read_files(
        self,
        files: list[str],
        working_dir: Path | None,
    ) -> dict[str, str]:
        """Read file contents, validating paths.

        Args:
            files: List of file paths to read.
            working_dir: Optional working directory to resolve relative paths.

        Returns:
            Dictionary mapping file paths to their contents.

        Raises:
            ValueError: If a file does not exist or is too large.
        """
        contents: dict[str, str] = {}

        for file_path_str in files:
            file_path = Path(file_path_str)

            # Resolve relative paths against working_dir
            if not file_path.is_absolute() and working_dir:
                file_path = working_dir / file_path

            file_path = file_path.resolve()

            if not file_path.is_file():
                raise ValueError(f"File not found: {file_path_str}")

            # Check file size
            size = file_path.stat().st_size
            if size > self.MAX_FILE_SIZE:
                raise ValueError(
                    f"File too large ({size} bytes): {file_path_str}. "
                    f"Maximum: {self.MAX_FILE_SIZE} bytes"
                )

            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raise ValueError(f"Cannot read file as text: {file_path_str}")

            contents[file_path_str] = content

        return contents

    # ------------------------------------------------------------------
    # Task formatting
    # ------------------------------------------------------------------

    def _format_task(
        self,
        task: str,
        files: dict[str, str],
        language: str | None,
    ) -> str:
        """Create a structured task prompt for the coding agent.

        Args:
            task: The coding task description.
            files: Dictionary of file paths to contents.
            language: Optional programming language hint.

        Returns:
            Formatted task string with context.
        """
        parts: list[str] = []

        parts.append("## Coding Task")
        parts.append("")
        parts.append(task)
        parts.append("")

        if language:
            parts.append(f"**Language:** {language}")
            parts.append("")

        if files:
            parts.append("## Context Files")
            parts.append("")
            for path, content in files.items():
                parts.append(f"### `{path}`")
                parts.append("```")
                parts.append(content)
                parts.append("```")
                parts.append("")

        parts.append("## Instructions")
        parts.append("")
        parts.append("Complete the task described above. Follow these guidelines:")
        parts.append("- Write clean, well-documented code")
        parts.append("- Include type hints where applicable")
        parts.append("- Handle errors gracefully")
        parts.append("- Follow the existing code style in the context files")

        return "\n".join(parts)
