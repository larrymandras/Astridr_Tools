"""Web search tool — search the web and fetch page content."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

# Default user-agent for fetching pages
_USER_AGENT = (
    "Mozilla/5.0 (compatible; Astridr/1.0; +https://github.com/larrymandras/astridr)"
)


class WebSearchTool(BaseTool):
    """Search the web and retrieve page content.

    Uses DuckDuckGo HTML search (no API key required) and httpx for
    fetching URLs. Checks for ``llms.txt`` on domains before fetching.
    """

    name = "web_search"
    description = "Search the web and retrieve page content"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "fetch"],
                "description": "The operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "url": {
                "type": "string",
                "description": "URL to fetch.",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Maximum number of search results.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, search_api: str = "duckduckgo") -> None:
        self._search_api = search_api
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
        """Dispatch to search or fetch action."""
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        dispatch = {
            "search": self._search,
            "fetch": self._fetch,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPError as exc:
            log.error("web_search.http_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _search(self, **kwargs: Any) -> ToolResult:
        """Search DuckDuckGo via HTML scraping (no API key needed)."""
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for search")
        limit = kwargs.get("limit", 10)

        client = self._ensure_client()
        resp = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
        )
        resp.raise_for_status()

        results = self._parse_ddg_html(resp.text, limit)

        lines = [f"{r['title']}\n  {r['url']}\n  {r['snippet']}" for r in results]
        log.info("web_search.search", query=query, count=len(results))
        return ToolResult(
            success=True,
            output="\n\n".join(lines) if lines else "No results found.",
            data={"results": results},
        )

    async def _fetch(self, **kwargs: Any) -> ToolResult:
        """Fetch URL content. Checks for llms.txt first."""
        url = kwargs.get("url", "")
        if not url:
            return ToolResult(success=False, error="url is required for fetch")

        # Check for llms.txt on the domain
        llms_info = await self._check_llms_txt(url)

        client = self._ensure_client()
        resp = await client.get(url)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            text = self._extract_text(resp.text)
        else:
            text = resp.text

        data: dict[str, Any] = {
            "url": str(resp.url),
            "status_code": resp.status_code,
            "content_type": content_type,
        }
        if llms_info:
            data["llms_txt"] = llms_info

        log.info("web_search.fetch", url=url, length=len(text))
        return ToolResult(success=True, output=text, data=data)

    async def _check_llms_txt(self, url: str) -> dict[str, Any] | None:
        """Check for /.well-known/llms.txt or /llms.txt on the domain.

        Returns parsed directives if found, None otherwise.
        """
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        client = self._ensure_client()

        for path in ("/.well-known/llms.txt", "/llms.txt"):
            try:
                check_url = urljoin(base, path)
                resp = await client.get(check_url)
                if resp.status_code == 200:
                    directives = self._parse_llms_txt(resp.text)
                    log.info("web_search.llms_txt_found", url=check_url)
                    return directives
            except httpx.HTTPError:
                continue

        return None

    @staticmethod
    def _parse_llms_txt(text: str) -> dict[str, Any]:
        """Parse llms.txt content into structured directives."""
        directives: dict[str, Any] = {"raw": text, "entries": []}

        for line in text.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Simple key: value parsing
            if ":" in line:
                key, _, value = line.partition(":")
                directives["entries"].append(
                    {"key": key.strip(), "value": value.strip()}
                )

        return directives

    @staticmethod
    def _parse_ddg_html(html: str, limit: int) -> list[dict[str, str]]:
        """Parse DuckDuckGo HTML results into structured results.

        This is a simple regex-based parser for the DDG HTML search page.
        """
        results: list[dict[str, str]] = []

        # Match result links and snippets from DDG HTML
        link_pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        snippet_pattern = re.compile(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        links = link_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i, (url, title) in enumerate(links[:limit]):
            snippet = snippets[i] if i < len(snippets) else ""
            results.append(
                {
                    "title": _strip_tags(title).strip(),
                    "url": url.strip(),
                    "snippet": _strip_tags(snippet).strip(),
                }
            )

        return results

    @staticmethod
    def _extract_text(html: str) -> str:
        """Extract readable text from HTML by stripping tags.

        Removes script/style blocks, then strips remaining HTML tags.
        """
        # Remove script and style blocks
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        # Replace block-level tags with newlines
        text = re.sub(r"<(?:p|div|br|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
        # Strip remaining tags
        text = re.sub(r"<[^>]+>", "", text)
        # Decode common HTML entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
        # Collapse whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    @staticmethod
    def _is_read_only_action(action: str) -> bool:
        """All web search actions are read-only."""
        return True

    def is_read_only(self, action: str) -> bool:
        """All web search actions are read-only."""
        return True


def _strip_tags(html: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", html)
