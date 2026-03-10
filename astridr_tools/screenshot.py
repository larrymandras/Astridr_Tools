"""Screenshot capture and vision analysis tools."""

from __future__ import annotations

import asyncio
import base64
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from astridr.providers.base import BaseProvider, Message
from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

# Maximum image file size for vision analysis: 20 MB
_MAX_IMAGE_SIZE = 20 * 1024 * 1024


class ScreenshotTool(BaseTool):
    """Capture screenshots of the full screen, a specific window, or a region.

    Screenshots are saved as PNG files with timestamp-based filenames.
    """

    name = "screenshot"
    description = "Capture screen or window screenshots for visual analysis"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["capture", "capture_window", "capture_region"],
                "description": "Type of screenshot to capture",
            },
            "title": {
                "type": "string",
                "description": "Window title to capture (for capture_window)",
            },
            "region": {
                "type": "object",
                "description": "Region {x, y, width, height} (for capture_region)",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
            },
        },
        "required": ["action"],
    }

    def __init__(self, output_dir: Path | None = None) -> None:
        self._output_dir = output_dir or Path.home() / ".astridr" / "media" / "screenshots"

    # ------------------------------------------------------------------
    # BaseTool.execute
    # ------------------------------------------------------------------

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Capture a screenshot based on the requested action."""
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        # Ensure output directory exists
        self._output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}_{uuid.uuid4().hex[:8]}.png"
        output_path = self._output_dir / filename

        try:
            if action == "capture":
                await self._capture_full(output_path)
            elif action == "capture_window":
                title = kwargs.get("title", "")
                if not title:
                    return ToolResult(
                        success=False,
                        error="title is required for capture_window",
                    )
                await self._capture_window(output_path, title)
            elif action == "capture_region":
                region = kwargs.get("region")
                if not region:
                    return ToolResult(
                        success=False,
                        error="region is required for capture_region",
                    )
                await self._capture_region(output_path, region)
            else:
                return ToolResult(success=False, error=f"Unknown action: {action}")

            log.info("screenshot.captured", action=action, path=str(output_path))
            return ToolResult(
                success=True,
                output=str(output_path),
                data={"path": str(output_path), "action": action},
            )
        except Exception as exc:
            log.error("screenshot.failed", action=action, error=str(exc))
            return ToolResult(success=False, error=f"Screenshot failed: {exc}")

    # ------------------------------------------------------------------
    # Capture methods
    # ------------------------------------------------------------------

    async def _capture_full(self, output_path: Path) -> None:
        """Capture the entire screen."""
        await asyncio.to_thread(self._capture_full_sync, output_path)

    def _capture_full_sync(self, output_path: Path) -> None:
        """Synchronous full-screen capture via Pillow ImageGrab."""
        from PIL import ImageGrab  # type: ignore[import-untyped]

        img = ImageGrab.grab()
        img.save(str(output_path), "PNG")

    async def _capture_window(self, output_path: Path, title: str) -> None:
        """Capture a specific window by title.

        Uses platform-specific methods: PowerShell on Windows for window
        geometry lookup, then crops the full screenshot.
        """
        await asyncio.to_thread(self._capture_window_sync, output_path, title)

    def _capture_window_sync(self, output_path: Path, title: str) -> None:
        """Synchronous window capture."""
        import sys

        from PIL import ImageGrab  # type: ignore[import-untyped]

        if sys.platform == "win32":
            # Use PowerShell to find window rect
            ps_script = (
                f'Add-Type -AssemblyName System.Windows.Forms; '
                f'$procs = Get-Process | Where-Object {{$_.MainWindowTitle -like "*{title}*"}}; '
                f'if ($procs) {{ $h = $procs[0].MainWindowHandle; '
                f'Add-Type @"'
                f'\nusing System; using System.Runtime.InteropServices;'
                f'\npublic class Win32 {{ [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect); }}'
                f'\npublic struct RECT {{ public int Left; public int Top; public int Right; public int Bottom; }}'
                f'\n"@;'
                f' $r = New-Object RECT; [Win32]::GetWindowRect($h, [ref]$r); '
                f'"$($r.Left),$($r.Top),$($r.Right),$($r.Bottom)" }}'
            )
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0 or not result.stdout.strip():
                raise RuntimeError(f"Window not found: {title}")

            coords = [int(x) for x in result.stdout.strip().split(",")]
            bbox = (coords[0], coords[1], coords[2], coords[3])
            img = ImageGrab.grab(bbox=bbox)
        else:
            # Fallback: full screen capture on non-Windows
            img = ImageGrab.grab()

        img.save(str(output_path), "PNG")

    async def _capture_region(self, output_path: Path, region: dict[str, Any]) -> None:
        """Capture a specific region of the screen."""
        await asyncio.to_thread(self._capture_region_sync, output_path, region)

    def _capture_region_sync(self, output_path: Path, region: dict[str, Any]) -> None:
        """Synchronous region capture."""
        from PIL import ImageGrab  # type: ignore[import-untyped]

        x = region.get("x", 0)
        y = region.get("y", 0)
        width = region.get("width", 100)
        height = region.get("height", 100)

        bbox = (x, y, x + width, y + height)
        img = ImageGrab.grab(bbox=bbox)
        img.save(str(output_path), "PNG")


class VisionAnalyzer(BaseTool):
    """Analyze images using vision-capable LLM models.

    Encodes images as base64 and sends them to a vision-capable provider
    (e.g. GPT-4o, Claude) for analysis.
    """

    name = "analyze_image"
    description = "Analyze images with AI vision to describe content, extract text, or answer questions"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "image_path": {
                "type": "string",
                "description": "Path to image file",
            },
            "question": {
                "type": "string",
                "description": "Question about the image",
                "default": "Describe this image in detail.",
            },
        },
        "required": ["image_path"],
    }

    SUPPORTED_FORMATS: frozenset[str] = frozenset(
        {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
    )

    def __init__(self, provider: BaseProvider | None = None) -> None:
        self._provider = provider

    # ------------------------------------------------------------------
    # BaseTool.execute
    # ------------------------------------------------------------------

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Analyze an image and return a textual description or answer."""
        image_path_str: str = kwargs.get("image_path", "")
        question: str = kwargs.get("question", "Describe this image in detail.")

        if not image_path_str:
            return ToolResult(success=False, error="Missing required parameter: image_path")

        if self._provider is None:
            return ToolResult(success=False, error="No vision provider configured")

        try:
            image_path = self._validate_image(image_path_str)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        try:
            encoded = self._encode_image(image_path)

            # Build a message with the image for the vision model
            content = (
                f"[Image: data:image/{image_path.suffix.lstrip('.')};base64,{encoded}]\n\n"
                f"{question}"
            )
            messages = [Message(role="user", content=content)]
            response = await self._provider.chat(messages=messages)

            analysis = response.content or "No analysis returned."
            log.info(
                "vision.analyzed",
                path=str(image_path),
                chars=len(analysis),
            )
            return ToolResult(
                success=True,
                output=analysis,
                data={"path": str(image_path), "question": question},
            )
        except Exception as exc:
            log.error("vision.failed", path=str(image_path), error=str(exc))
            return ToolResult(success=False, error=f"Vision analysis failed: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _encode_image(self, path: Path) -> str:
        """Read an image file and return its base64-encoded content."""
        raw = path.read_bytes()
        return base64.b64encode(raw).decode("ascii")

    def _validate_image(self, path: str) -> Path:
        """Validate that the image file exists, has a supported format, and is not too large.

        Args:
            path: String path to the image file.

        Returns:
            Resolved Path object.

        Raises:
            ValueError: If validation fails.
        """
        image_path = Path(path).resolve()

        if not image_path.exists():
            raise ValueError(f"Image file not found: {image_path}")

        suffix = image_path.suffix.lower()
        if suffix not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported image format '{suffix}'. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_FORMATS))}"
            )

        file_size = image_path.stat().st_size
        if file_size > _MAX_IMAGE_SIZE:
            raise ValueError(
                f"Image file too large ({file_size / 1024 / 1024:.1f} MB). "
                f"Maximum: {_MAX_IMAGE_SIZE / 1024 / 1024:.0f} MB"
            )

        return image_path
