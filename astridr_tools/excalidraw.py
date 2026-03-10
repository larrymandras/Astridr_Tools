"""Excalidraw integration tool — local diagram scene builder.

No API key required. Builds Excalidraw v2 JSON scenes in memory and exports to .excalidraw files.
"""

from __future__ import annotations

import json
import random
import uuid
from pathlib import Path
from typing import Any

import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

_ELEMENT_TYPES = frozenset({"rectangle", "ellipse", "diamond", "text", "arrow", "line"})


class ExcalidrawTool(BaseTool):
    """Create diagrams as Excalidraw scenes: shapes, text, arrows, export."""

    name = "excalidraw"
    description = "Build Excalidraw diagram scenes — create scenes, add elements, export .excalidraw files"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create_scene", "add_element", "export_scene", "get_scene"],
                "description": "The Excalidraw operation to perform.",
            },
            "scene_id": {
                "type": "string",
                "description": "Scene ID for operations on existing scenes.",
            },
            "element_type": {
                "type": "string",
                "enum": ["rectangle", "ellipse", "diamond", "text", "arrow", "line"],
                "description": "Type of element to add.",
            },
            "x": {
                "type": "number",
                "description": "X position (default 0).",
                "default": 0,
            },
            "y": {
                "type": "number",
                "description": "Y position (default 0).",
                "default": 0,
            },
            "width": {
                "type": "number",
                "description": "Element width (default 200).",
                "default": 200,
            },
            "height": {
                "type": "number",
                "description": "Element height (default 100).",
                "default": 100,
            },
            "text": {
                "type": "string",
                "description": "Text content for text elements.",
            },
            "stroke_color": {
                "type": "string",
                "description": "Stroke color (default '#000000').",
                "default": "#000000",
            },
            "background_color": {
                "type": "string",
                "description": "Background color (default 'transparent').",
                "default": "transparent",
            },
            "fill_style": {
                "type": "string",
                "enum": ["hachure", "cross-hatch", "solid"],
                "description": "Fill style (default 'hachure').",
                "default": "hachure",
            },
            "points": {
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "number"},
                },
                "description": "Points for arrow/line elements as [[x,y], ...].",
            },
            "filename": {
                "type": "string",
                "description": "Output filename for export (without extension).",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset({"get_scene"})

    def __init__(self, output_dir: Path | None = None) -> None:
        self._output_dir = output_dir or Path.cwd()
        self._scenes: dict[str, dict] = {}

    async def close(self) -> None:
        pass

    def is_read_only(self, action: str) -> bool:
        return action in self._READ_ACTIONS

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        dispatch = {
            "create_scene": self._create_scene,
            "add_element": self._add_element,
            "export_scene": self._export_scene,
            "get_scene": self._get_scene,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        return await handler(**kwargs)

    async def _create_scene(self, **kwargs: Any) -> ToolResult:
        scene_id = str(uuid.uuid4())
        self._scenes[scene_id] = {
            "type": "excalidraw",
            "version": 2,
            "source": "astridr",
            "elements": [],
            "appState": {"viewBackgroundColor": "#ffffff"},
            "files": {},
        }
        log.info("excalidraw.scene_created", scene_id=scene_id)
        return ToolResult(
            success=True,
            output=f"Scene created: {scene_id}",
            data={"scene_id": scene_id},
        )

    async def _add_element(self, **kwargs: Any) -> ToolResult:
        scene_id = kwargs.get("scene_id", "")
        if not scene_id or scene_id not in self._scenes:
            return ToolResult(success=False, error="Invalid or missing scene_id")

        element_type = kwargs.get("element_type", "")
        if element_type not in _ELEMENT_TYPES:
            return ToolResult(
                success=False,
                error=f"Invalid element_type: {element_type}. Must be one of: {', '.join(sorted(_ELEMENT_TYPES))}",
            )

        element_id = str(uuid.uuid4())
        element: dict[str, Any] = {
            "id": element_id,
            "type": element_type,
            "x": kwargs.get("x", 0),
            "y": kwargs.get("y", 0),
            "width": kwargs.get("width", 200),
            "height": kwargs.get("height", 100),
            "angle": 0,
            "strokeColor": kwargs.get("stroke_color", "#000000"),
            "backgroundColor": kwargs.get("background_color", "transparent"),
            "fillStyle": kwargs.get("fill_style", "hachure"),
            "strokeWidth": 1,
            "strokeStyle": "solid",
            "roughness": 1,
            "opacity": 100,
            "groupIds": [],
            "roundness": None,
            "version": 1,
            "versionNonce": random.randint(0, 2**31),
            "seed": random.randint(0, 2**31),
            "isDeleted": False,
            "boundElements": None,
            "updated": 1,
            "link": None,
            "locked": False,
        }

        if element_type == "text":
            element["text"] = kwargs.get("text", "")
            element["fontSize"] = 20
            element["fontFamily"] = 1
            element["textAlign"] = "left"
            element["verticalAlign"] = "top"
            element["baseline"] = 18

        if element_type in ("arrow", "line"):
            element["points"] = kwargs.get("points", [[0, 0], [200, 0]])
            element["lastCommittedPoint"] = None
            element["startBinding"] = None
            element["endBinding"] = None
            element["startArrowhead"] = None
            element["endArrowhead"] = "arrow" if element_type == "arrow" else None

        self._scenes[scene_id]["elements"].append(element)
        log.info("excalidraw.element_added", scene_id=scene_id, element_type=element_type)
        return ToolResult(
            success=True,
            output=f"Added {element_type} element: {element_id}",
            data={"element_id": element_id, "scene_id": scene_id},
        )

    async def _export_scene(self, **kwargs: Any) -> ToolResult:
        scene_id = kwargs.get("scene_id", "")
        if not scene_id or scene_id not in self._scenes:
            return ToolResult(success=False, error="Invalid or missing scene_id")

        filename = kwargs.get("filename", scene_id)
        output_path = self._output_dir / f"{filename}.excalidraw"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self._scenes[scene_id], indent=2), encoding="utf-8"
        )
        log.info("excalidraw.scene_exported", path=str(output_path))
        return ToolResult(
            success=True,
            output=f"Exported scene to: {output_path}",
            data={"path": str(output_path), "scene_id": scene_id},
        )

    async def _get_scene(self, **kwargs: Any) -> ToolResult:
        scene_id = kwargs.get("scene_id", "")
        if not scene_id or scene_id not in self._scenes:
            return ToolResult(success=False, error="Invalid or missing scene_id")

        scene = self._scenes[scene_id]
        element_count = len(scene["elements"])
        return ToolResult(
            success=True,
            output=f"Scene {scene_id}: {element_count} element(s)",
            data={"scene": scene, "scene_id": scene_id, "element_count": element_count},
        )
