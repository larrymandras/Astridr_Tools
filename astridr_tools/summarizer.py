"""Content summarizer tool — summarize URLs, articles, and text."""

from __future__ import annotations

import re
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

# Default user-agent for fetching pages
_USER_AGENT = (
    "Mozilla/5.0 (compatible; Astridr/1.0; +https://github.com/larrymandras/astridr)"
)


class SummarizerTool(BaseTool):
    """Summarize web pages, articles, and content.

    Fetches URL content (if URL provided) or uses provided text,
    then returns prepared/truncated content for the LLM to summarize.
    """

    name = "summarize"
    description = "Summarize URLs, articles, or long text content"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to summarize.",
            },
            "text": {
                "type": "string",
                "description": "Text content to summarize.",
            },
            "max_length": {
                "type": "integer",
                "description": "Max summary length in words.",
                "default": 200,
            },
            "style": {
                "type": "string",
                "enum": ["brief", "detailed", "bullet_points"],
                "default": "brief",
                "description": "Summary style.",
            },
        },
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create the httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": _USER_AGENT},
                timeout=30.0,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Fetch URL content or use provided text, prepare for summarization."""
        url = kwargs.get("url")
        text = kwargs.get("text")
        max_length = kwargs.get("max_length", 200)
        style = kwargs.get("style", "brief")

        if not url and not text:
            return ToolResult(
                success=False,
                error="Either url or text is required for summarization.",
            )

        # Get content
        if url:
            try:
                content = await self._fetch_content(url)
            except httpx.HTTPError as exc:
                log.error("summarizer.fetch_error", url=url, error=str(exc))
                return ToolResult(
                    success=False, error=f"Failed to fetch URL: {exc}"
                )
        else:
            content = text or ""

        # Truncate to reasonable size
        content = self._truncate_content(content)

        if not content.strip():
            return ToolResult(
                success=False,
                error="No content could be extracted for summarization.",
            )

        # Build instruction for LLM
        style_instructions = {
            "brief": f"Provide a brief summary in no more than {max_length} words.",
            "detailed": f"Provide a detailed summary in no more than {max_length} words, "
            "covering the main points and key details.",
            "bullet_points": f"Provide a summary as bullet points, "
            f"no more than {max_length} words total.",
        }
        instruction = style_instructions.get(style, style_instructions["brief"])

        output = (
            f"[Summarization request \u2014 {style}]\n"
            f"Instruction: {instruction}\n\n"
            f"Content to summarize:\n"
            f"---\n"
            f"{content}\n"
            f"---"
        )

        log.info(
            "summarizer.prepared",
            url=url,
            style=style,
            content_length=len(content),
        )
        return ToolResult(
            success=True,
            output=output,
            data={
                "source": url or "text",
                "content_length": len(content),
                "style": style,
                "max_length": max_length,
            },
        )

    async def _fetch_content(self, url: str) -> str:
        """Fetch and extract text from a URL."""
        client = self._ensure_client()
        resp = await client.get(url)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            return self._extract_text(resp.text)
        return resp.text

    @staticmethod
    def _extract_text(html: str) -> str:
        """Extract readable text from HTML by stripping tags."""
        # Remove script and style blocks
        text = re.sub(
            r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE
        )
        text = re.sub(
            r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE
        )
        # Replace block-level tags with newlines
        text = re.sub(
            r"<(?:p|div|br|h[1-6]|li|tr)[^>]*>",
            "\n",
            text,
            flags=re.IGNORECASE,
        )
        # Strip remaining tags
        text = re.sub(r"<[^>]+>", "", text)
        # Decode common entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
        # Collapse whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    @staticmethod
    def _truncate_content(text: str, max_chars: int = 50000) -> str:
        """Smart truncation preserving paragraph boundaries.

        Truncates at the last paragraph boundary before max_chars.
        """
        if len(text) <= max_chars:
            return text

        # Find the last paragraph break before the limit
        truncated = text[:max_chars]
        last_para = truncated.rfind("\n\n")
        if last_para > max_chars // 2:
            # Found a reasonable paragraph break
            return truncated[:last_para] + "\n\n[Content truncated...]"

        # Fall back to last newline
        last_nl = truncated.rfind("\n")
        if last_nl > max_chars // 2:
            return truncated[:last_nl] + "\n\n[Content truncated...]"

        # Hard truncate
        return truncated + "\n\n[Content truncated...]"

    def is_read_only(self, action: str = "") -> bool:
        """Summarizer is always read-only."""
        return True
