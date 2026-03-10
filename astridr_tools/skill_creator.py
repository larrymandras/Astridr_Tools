"""Skill Creator Tool — dynamically creates new skills at runtime.

Allows the agent to create new tool skills from a description and Python
code. Generated skills are placed under the skills root and can be loaded
by the SkillManager.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path
from typing import Any

import structlog

from astridr.engine.atomic_io import atomic_write
from astridr.tools.base import BaseTool, ToolResult

logger = structlog.get_logger()

# Imports/calls that are blocked from skill code for safety
_DANGEROUS_PATTERNS: list[str] = [
    "os.system",
    "subprocess",
    "eval",
    "exec",
    "__import__",
    "importlib",
    "ctypes",
    "shutil.rmtree",
]

# Regex for valid kebab-case names: lowercase letters, digits, hyphens.
# Must start with a letter and not end with a hyphen.
_KEBAB_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class SkillCreatorTool(BaseTool):
    """Create new tool skills on the fly.

    The agent can invoke this tool to generate a skill directory with
    SKILL.md, tool.py, and an optional requirements.txt. The generated
    tool.py contains a BaseTool subclass wrapping the provided Python code.
    """

    name = "create_skill"
    description = "Create a new tool skill from a description and Python code"
    approval_tier = "supervised"

    PARAMETERS: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name (kebab-case, e.g. 'my-new-skill')",
            },
            "description": {
                "type": "string",
                "description": "One-line description of what the skill does",
            },
            "tool_name": {
                "type": "string",
                "description": "Tool name the agent will use to call this skill",
            },
            "parameters_schema": {
                "type": "object",
                "description": "JSON Schema of tool parameters",
            },
            "python_code": {
                "type": "string",
                "description": "Python async function body for execute()",
            },
            "dependencies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "pip packages needed by this skill",
            },
        },
        "required": ["name", "description", "tool_name", "python_code"],
    }

    def __init__(self, skills_root: Path | None = None) -> None:
        self._skills_root = skills_root or Path.home() / ".astridr" / "skills"
        # Satisfy BaseTool.parameters (used by to_definition())
        self.parameters = self.PARAMETERS
        # Hot-load targets — injected after bootstrap wiring
        self._skill_manager: Any = None
        self._registry: Any = None

    def set_hot_load(self, *, skill_mgr: Any, registry: Any) -> None:
        """Inject SkillManager and ToolRegistry for hot-loading new skills.

        Called by bootstrap after both the skill manager and registry are ready.
        When set, newly created skills are immediately loaded and their tools
        registered — no restart required.
        """
        self._skill_manager = skill_mgr
        self._registry = registry

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Create a new skill from the provided specification.

        Args:
            **kwargs: Must include name, description, tool_name, python_code.
                Optionally: parameters_schema, dependencies.

        Returns:
            ToolResult with success status and path to the created skill.
        """
        # Extract and validate required fields
        skill_name: str | None = kwargs.get("name")
        description: str | None = kwargs.get("description")
        tool_name: str | None = kwargs.get("tool_name")
        python_code: str | None = kwargs.get("python_code")
        parameters_schema: dict[str, Any] | None = kwargs.get("parameters_schema")
        dependencies: list[str] | None = kwargs.get("dependencies")

        # Check required fields
        if not skill_name:
            return ToolResult(success=False, error="Missing required field: name")
        if not description:
            return ToolResult(success=False, error="Missing required field: description")
        if not tool_name:
            return ToolResult(success=False, error="Missing required field: tool_name")
        if not python_code:
            return ToolResult(success=False, error="Missing required field: python_code")

        # Validate skill name
        try:
            validated_name = self._validate_name(skill_name)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        # Check for existing skill with same name
        skill_dir = self._skills_root / validated_name
        resolved_dir = self._validate_path(skill_dir)
        if resolved_dir.exists():
            return ToolResult(
                success=False,
                error=f"Skill already exists: {validated_name}",
            )

        # Validate Python code
        if not self._validate_python(python_code):
            return ToolResult(
                success=False,
                error="Python code validation failed: syntax error or dangerous imports detected",
            )

        # Generate file contents
        skill_md = self._generate_skill_md(validated_name, description, tool_name, parameters_schema)
        tool_py = self._generate_tool_py(tool_name, description, parameters_schema, python_code)

        # Write files atomically
        try:
            resolved_dir.mkdir(parents=True, exist_ok=True)

            await atomic_write(resolved_dir / "SKILL.md", skill_md)
            await atomic_write(resolved_dir / "tool.py", tool_py)

            if dependencies:
                requirements = "\n".join(dependencies) + "\n"
                await atomic_write(resolved_dir / "requirements.txt", requirements)

            logger.info(
                "skill_creator.created",
                name=validated_name,
                tool_name=tool_name,
                path=str(resolved_dir),
            )

            # Hot-load: immediately register the new skill and its tool
            hot_loaded = await self._hot_load_skill(validated_name, resolved_dir, tool_name)

            return ToolResult(
                success=True,
                output=f"Skill '{validated_name}' created at {resolved_dir}"
                + (" (hot-loaded)" if hot_loaded else " (restart to activate)"),
                data={
                    "skill_name": validated_name,
                    "tool_name": tool_name,
                    "path": str(resolved_dir),
                    "hot_loaded": hot_loaded,
                },
            )

        except Exception as exc:
            logger.error(
                "skill_creator.write_failed",
                name=validated_name,
                error=str(exc),
            )
            return ToolResult(
                success=False,
                error=f"Failed to write skill files: {exc}",
            )

    # ------------------------------------------------------------------
    # Hot-loading
    # ------------------------------------------------------------------

    async def _hot_load_skill(
        self, skill_name: str, skill_dir: Path, tool_name: str
    ) -> bool:
        """Load a newly created skill into SkillManager and register its tool.

        Returns True if hot-loading succeeded, False if skipped or failed.
        """
        if self._skill_manager is None or self._registry is None:
            return False

        try:
            await self._skill_manager.load(skill_name)
            logger.info("skill_creator.hot_loaded_skill", name=skill_name)
        except Exception as exc:
            logger.warning(
                "skill_creator.hot_load_skill_failed",
                name=skill_name,
                error=str(exc),
            )
            return False

        # If the skill has a tool.py, dynamically import and register it
        tool_py = skill_dir / "tool.py"
        if not tool_py.exists():
            return True

        try:
            import importlib.util as _ilu

            module_name = f"astridr_skill_{skill_name.replace('-', '_')}_tool"
            spec = _ilu.spec_from_file_location(module_name, tool_py)
            if spec is None or spec.loader is None:
                logger.warning("skill_creator.tool_import_failed", name=skill_name)
                return True  # Skill loaded, just tool import failed

            module = _ilu.module_from_spec(spec)
            import sys as _sys

            _sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Find the BaseTool subclass in the module
            from astridr.tools.base import BaseTool as _BT

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, _BT)
                    and attr is not _BT
                ):
                    tool_instance = attr()
                    self._registry.register(tool_instance)
                    logger.info(
                        "skill_creator.hot_registered_tool",
                        skill=skill_name,
                        tool=tool_instance.name,
                    )
                    break

        except Exception as exc:
            logger.warning(
                "skill_creator.tool_register_failed",
                name=skill_name,
                error=str(exc),
            )

        return True

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_name(self, name: str) -> str:
        """Validate that a skill name is kebab-case with no special characters.

        Args:
            name: Proposed skill name.

        Returns:
            The validated name.

        Raises:
            ValueError: If the name is invalid.
        """
        if not name:
            raise ValueError("Skill name cannot be empty")

        if not _KEBAB_CASE_RE.match(name):
            raise ValueError(
                f"Invalid skill name '{name}'. "
                "Must be kebab-case (lowercase letters, digits, hyphens). "
                "Must start with a letter."
            )

        if len(name) > 64:
            raise ValueError(f"Skill name too long ({len(name)} chars, max 64)")

        return name

    def _validate_python(self, code: str) -> bool:
        """Validate Python code for syntax correctness and safety.

        Uses ``ast.parse()`` to check syntax and scans for dangerous
        imports/calls (os.system, subprocess, eval, exec, etc.).

        Args:
            code: Python source code string.

        Returns:
            True if code is valid and safe, False otherwise.
        """
        # Check syntax
        try:
            ast.parse(code)
        except SyntaxError:
            logger.warning("skill_creator.syntax_error", code_preview=code[:100])
            return False

        # Check for dangerous patterns
        for pattern in _DANGEROUS_PATTERNS:
            if pattern in code:
                logger.warning(
                    "skill_creator.dangerous_pattern",
                    pattern=pattern,
                    code_preview=code[:100],
                )
                return False

        return True

    def _validate_path(self, path: Path) -> Path:
        """Ensure a path is within the skills root (no directory traversal).

        Args:
            path: Path to validate.

        Returns:
            The resolved path.

        Raises:
            ValueError: If the path escapes the skills root.
        """
        resolved = path.resolve()
        if not resolved.is_relative_to(self._skills_root.resolve()):
            raise ValueError(f"Path escapes skills root: {path}")
        return resolved

    # ------------------------------------------------------------------
    # Code generation helpers
    # ------------------------------------------------------------------

    def _generate_skill_md(
        self,
        name: str,
        description: str,
        tool_name: str,
        parameters: dict[str, Any] | None,
    ) -> str:
        """Generate SKILL.md content for a new skill.

        Args:
            name: Skill name (kebab-case).
            description: One-line description.
            tool_name: Tool name used by the agent.
            parameters: Optional JSON Schema for parameters.

        Returns:
            Markdown string for SKILL.md.
        """
        lines = [
            f"# {name}",
            "",
            description,
            "",
            "## Tool",
            "",
            f"- **Name**: `{tool_name}`",
            f"- **Description**: {description}",
        ]

        if parameters:
            props = parameters.get("properties", {})
            if props:
                lines.append("")
                lines.append("## Parameters")
                lines.append("")
                for prop_name, prop_schema in props.items():
                    prop_type = prop_schema.get("type", "any")
                    prop_desc = prop_schema.get("description", "")
                    lines.append(f"- `{prop_name}` ({prop_type}): {prop_desc}")

        lines.append("")
        return "\n".join(lines)

    def _generate_tool_py(
        self,
        tool_name: str,
        description: str,
        parameters: dict[str, Any] | None,
        code: str,
    ) -> str:
        """Generate tool.py content with a BaseTool subclass.

        Args:
            tool_name: Tool name used by the agent.
            description: One-line description.
            parameters: Optional JSON Schema for parameters.
            code: Python async function body for execute().

        Returns:
            Python source code for tool.py.
        """
        # Build the parameters schema
        if parameters is None:
            params_repr = '{"type": "object", "properties": {}}'
        else:
            # Format as a compact but readable Python dict literal
            import json

            params_repr = json.dumps(parameters, indent=8)

        # Convert tool_name to a valid Python class name
        class_name = "".join(word.capitalize() for word in tool_name.split("_")) + "Tool"

        # Indent the user code to fit inside the method body
        indented_code = textwrap.indent(textwrap.dedent(code), "        ")

        return (
            '"""Auto-generated skill tool."""\n'
            "\n"
            "from __future__ import annotations\n"
            "\n"
            "from typing import Any\n"
            "\n"
            "from astridr.tools.base import BaseTool, ToolResult\n"
            "\n"
            "\n"
            f"class {class_name}(BaseTool):\n"
            f'    """Tool: {description}"""\n'
            "\n"
            f'    name = "{tool_name}"\n'
            f'    description = "{description}"\n'
            f"    parameters = {params_repr}\n"
            f'    approval_tier = "supervised"\n'
            "\n"
            "    async def execute(self, **kwargs: Any) -> ToolResult:\n"
            f"{indented_code}\n"
        )
