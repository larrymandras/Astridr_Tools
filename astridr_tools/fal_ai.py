"""fal.ai integration tool — AI image and video generation via queue API.

Requires FAL_KEY environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

FAL_QUEUE_BASE = "https://queue.fal.run"
FAL_RESULT_BASE = "https://queue.fal.run"


class FalAITool(BaseTool):
    """Generate images and videos using fal.ai models."""

    name = "fal_ai"
    description = "Generate AI images and videos via fal.ai queue-based API"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["generate_image", "generate_video", "get_result", "list_models"],
                "description": "The fal.ai operation to perform.",
            },
            "model": {
                "type": "string",
                "description": "Model identifier (e.g. 'fal-ai/flux/dev', 'fal-ai/runway-gen3/turbo').",
            },
            "prompt": {
                "type": "string",
                "description": "Text prompt for generation.",
            },
            "request_id": {
                "type": "string",
                "description": "Request ID from a previous submission (for get_result).",
            },
            "image_size": {
                "type": "string",
                "enum": ["square_hd", "square", "portrait_4_3", "portrait_16_9", "landscape_4_3", "landscape_16_9"],
                "description": "Image size preset.",
                "default": "landscape_4_3",
            },
            "num_images": {
                "type": "integer",
                "description": "Number of images to generate (default 1).",
                "default": 1,
            },
            "image_url": {
                "type": "string",
                "description": "Input image URL (for image-to-video).",
            },
            "duration": {
                "type": "integer",
                "description": "Video duration in seconds.",
            },
            "extra_params": {
                "type": "object",
                "description": "Additional model-specific parameters.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self._api_key = os.environ.get("FAL_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {}
            if self._api_key:
                headers["Authorization"] = f"Key {self._api_key}"
            headers["Content-Type"] = "application/json"
            self._client = httpx.AsyncClient(headers=headers, timeout=60.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")
        if not self._api_key:
            return ToolResult(success=False, error="FAL_KEY not configured")

        dispatch = {
            "generate_image": self._generate_image,
            "generate_video": self._generate_video,
            "get_result": self._get_result,
            "list_models": self._list_models,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("fal_ai.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"fal.ai API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("fal_ai.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _generate_image(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        model = kwargs.get("model", "fal-ai/flux/dev")
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return ToolResult(success=False, error="prompt is required for generate_image")

        payload: dict[str, Any] = {
            "prompt": prompt,
            "image_size": kwargs.get("image_size", "landscape_4_3"),
            "num_images": kwargs.get("num_images", 1),
        }
        if kwargs.get("extra_params"):
            payload.update(kwargs["extra_params"])

        resp = await client.post(f"{FAL_QUEUE_BASE}/{model}", json=payload)
        resp.raise_for_status()
        data = resp.json()
        request_id = data.get("request_id", "")
        log.info("fal_ai.image_submitted", model=model, request_id=request_id)
        return ToolResult(
            success=True,
            output=f"Image generation submitted: {request_id}\nUse get_result to check status.",
            data={"request_id": request_id, "model": model, "status": "queued"},
        )

    async def _generate_video(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        model = kwargs.get("model", "fal-ai/runway-gen3/turbo")
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return ToolResult(success=False, error="prompt is required for generate_video")

        payload: dict[str, Any] = {"prompt": prompt}
        if kwargs.get("image_url"):
            payload["image_url"] = kwargs["image_url"]
        if kwargs.get("duration"):
            payload["duration"] = kwargs["duration"]
        if kwargs.get("extra_params"):
            payload.update(kwargs["extra_params"])

        resp = await client.post(f"{FAL_QUEUE_BASE}/{model}", json=payload)
        resp.raise_for_status()
        data = resp.json()
        request_id = data.get("request_id", "")
        log.info("fal_ai.video_submitted", model=model, request_id=request_id)
        return ToolResult(
            success=True,
            output=f"Video generation submitted: {request_id}\nUse get_result to check status.",
            data={"request_id": request_id, "model": model, "status": "queued"},
        )

    async def _get_result(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        request_id = kwargs.get("request_id", "")
        model = kwargs.get("model", "")
        if not request_id:
            return ToolResult(success=False, error="request_id is required for get_result")
        if not model:
            return ToolResult(success=False, error="model is required for get_result")

        resp = await client.get(f"{FAL_RESULT_BASE}/{model}/requests/{request_id}/status")
        resp.raise_for_status()
        status_data = resp.json()
        status = status_data.get("status", "unknown")

        if status == "COMPLETED":
            result_resp = await client.get(f"{FAL_RESULT_BASE}/{model}/requests/{request_id}")
            result_resp.raise_for_status()
            result_data = result_resp.json()
            images = result_data.get("images", [])
            video = result_data.get("video", {})

            if images:
                urls = [img.get("url", "") for img in images]
                output = f"Completed! {len(images)} image(s):\n" + "\n".join(urls)
            elif video:
                output = f"Completed! Video: {video.get('url', '')}"
            else:
                output = f"Completed! Result: {str(result_data)[:500]}"

            return ToolResult(success=True, output=output, data={"result": result_data, "status": "COMPLETED"})

        return ToolResult(
            success=True,
            output=f"Status: {status}",
            data={"status": status, "request_id": request_id},
        )

    async def _list_models(self, **kwargs: Any) -> ToolResult:
        models = [
            {"id": "fal-ai/flux/dev", "type": "image", "description": "Flux Dev — high-quality image generation"},
            {"id": "fal-ai/flux/schnell", "type": "image", "description": "Flux Schnell — fast image generation"},
            {"id": "fal-ai/flux-pro/v1.1", "type": "image", "description": "Flux Pro v1.1 — professional quality"},
            {"id": "fal-ai/runway-gen3/turbo", "type": "video", "description": "Runway Gen-3 Turbo — video generation"},
            {"id": "fal-ai/stable-diffusion-v35-large", "type": "image", "description": "SD 3.5 Large"},
            {"id": "fal-ai/kling-video/v1.5/pro", "type": "video", "description": "Kling v1.5 Pro — video generation"},
        ]
        lines = [f"{m['id']} [{m['type']}] — {m['description']}" for m in models]
        return ToolResult(success=True, output="\n".join(lines), data={"models": models})
