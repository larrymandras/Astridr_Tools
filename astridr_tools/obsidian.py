"""Obsidian vault integration tool \u2014 search, read, create, and manage notes."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class ObsidianTool(BaseTool):
    """Interact with Obsidian vault files.

    Supports searching, reading, creating, appending, listing recent notes,
    and finding backlinks within an Obsidian vault directory.
    """

    name = "obsidian"
    description = "Search, read, create, and manage notes in your Obsidian vault"
    approval_tier = "supervised"  # Writes need approval

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "search",
                    "read",
                    "create",
                    "append",
                    "list_recent",
                    "find_backlinks",
                ],
                "description": "The vault operation to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "path": {
                "type": "string",
                "description": "Note path relative to vault root (e.g. 'daily/2024-01-15.md').",
            },
            "content": {
                "type": "string",
                "description": "Content for create or append operations.",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Maximum number of results to return.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset(
        {"search", "read", "list_recent", "find_backlinks"}
    )

    def __init__(self, vault_path: Path | None = None) -> None:
        self._vault_path = vault_path  # Resolved at execute time if None

    @property
    def vault_path(self) -> Path:
        """Resolve the vault path from the constructor arg or env var."""
        if self._vault_path is not None:
            return self._vault_path
        env = os.environ.get("OBSIDIAN_VAULT_PATH", "")
        if env:
            return Path(env)
        raise ValueError(
            "Obsidian vault path not configured. "
            "Set OBSIDIAN_VAULT_PATH or pass vault_path to ObsidianTool."
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Dispatch to the requested vault action."""
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        dispatch = {
            "search": self._search,
            "read": self._read,
            "create": self._create,
            "append": self._append,
            "list_recent": self._list_recent,
            "find_backlinks": self._find_backlinks,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except OSError as exc:
            log.error("obsidian.os_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"File system error: {exc}")

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def _search(self, **kwargs: Any) -> ToolResult:
        """Search vault files by content (case-insensitive grep-like)."""
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for search")
        limit = kwargs.get("limit", 10)

        vault = self.vault_path
        if not vault.is_dir():
            return ToolResult(success=False, error=f"Vault not found: {vault}")

        results: list[dict[str, Any]] = []
        pattern = re.compile(re.escape(query), re.IGNORECASE)

        for md_file in vault.rglob("*.md"):
            if len(results) >= limit:
                break
            try:
                text = md_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            matches = pattern.findall(text)
            if matches:
                rel = md_file.relative_to(vault)
                results.append(
                    {
                        "path": str(rel),
                        "matches": len(matches),
                        "preview": text[:200],
                    }
                )

        lines = [f"{r['path']} ({r['matches']} matches)" for r in results]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No results found.",
            data={"results": results},
        )

    async def _read(self, **kwargs: Any) -> ToolResult:
        """Read a note file, validating the path stays within the vault."""
        path_str = kwargs.get("path", "")
        if not path_str:
            return ToolResult(success=False, error="path is required for read")

        file_path = self._validate_vault_path(path_str)
        if not file_path.exists():
            return ToolResult(
                success=False, error=f"Note not found: {path_str}"
            )

        text = file_path.read_text(encoding="utf-8", errors="replace")
        log.info("obsidian.read", path=path_str)
        return ToolResult(
            success=True,
            output=text,
            data={"path": path_str, "length": len(text)},
        )

    async def _list_recent(self, **kwargs: Any) -> ToolResult:
        """List recently modified notes, sorted by modification time."""
        limit = kwargs.get("limit", 10)
        vault = self.vault_path
        if not vault.is_dir():
            return ToolResult(success=False, error=f"Vault not found: {vault}")

        md_files = list(vault.rglob("*.md"))
        # Sort by modification time, most recent first
        md_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        notes: list[dict[str, Any]] = []
        for f in md_files[:limit]:
            rel = f.relative_to(vault)
            notes.append(
                {
                    "path": str(rel),
                    "modified": f.stat().st_mtime,
                    "size": f.stat().st_size,
                }
            )

        lines = [n["path"] for n in notes]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No notes found.",
            data={"notes": notes},
        )

    async def _find_backlinks(self, **kwargs: Any) -> ToolResult:
        """Find notes that link to the given note via [[path]] wikilinks."""
        path_str = kwargs.get("path", "")
        if not path_str:
            return ToolResult(success=False, error="path is required for find_backlinks")

        vault = self.vault_path
        if not vault.is_dir():
            return ToolResult(success=False, error=f"Vault not found: {vault}")

        # Build link target: strip .md extension for wikilink matching
        link_target = path_str
        if link_target.endswith(".md"):
            link_target = link_target[:-3]
        # Also match just the filename without directory
        link_basename = Path(link_target).name

        # Pattern matches [[target]] or [[target|alias]]
        patterns = [
            re.compile(r"\[\[" + re.escape(link_target) + r"(\|[^\]]+)?\]\]"),
            re.compile(r"\[\[" + re.escape(link_basename) + r"(\|[^\]]+)?\]\]"),
        ]

        results: list[dict[str, str]] = []
        for md_file in vault.rglob("*.md"):
            rel = str(md_file.relative_to(vault))
            # Don't include self-references
            if rel == path_str:
                continue
            try:
                text = md_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for pat in patterns:
                if pat.search(text):
                    results.append({"path": rel})
                    break

        lines = [r["path"] for r in results]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No backlinks found.",
            data={"backlinks": results},
        )

    # ------------------------------------------------------------------
    # Write operations (supervised)
    # ------------------------------------------------------------------

    async def _create(self, **kwargs: Any) -> ToolResult:
        """Create a new note in the vault."""
        path_str = kwargs.get("path", "")
        content = kwargs.get("content", "")
        if not path_str:
            return ToolResult(success=False, error="path is required for create")

        file_path = self._validate_vault_path(path_str)
        if file_path.exists():
            return ToolResult(
                success=False, error=f"Note already exists: {path_str}"
            )

        # Ensure parent directories exist
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

        log.info("obsidian.created", path=path_str)
        return ToolResult(
            success=True,
            output=f"Created note: {path_str}",
            data={"path": path_str},
        )

    async def _append(self, **kwargs: Any) -> ToolResult:
        """Append content to an existing note."""
        path_str = kwargs.get("path", "")
        content = kwargs.get("content", "")
        if not path_str:
            return ToolResult(success=False, error="path is required for append")
        if not content:
            return ToolResult(success=False, error="content is required for append")

        file_path = self._validate_vault_path(path_str)
        if not file_path.exists():
            return ToolResult(
                success=False, error=f"Note not found: {path_str}"
            )

        existing = file_path.read_text(encoding="utf-8", errors="replace")
        # Append with a newline separator if existing content doesn't end with one
        separator = "" if existing.endswith("\n") else "\n"
        file_path.write_text(existing + separator + content, encoding="utf-8")

        log.info("obsidian.appended", path=path_str)
        return ToolResult(
            success=True,
            output=f"Appended to note: {path_str}",
            data={"path": path_str},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_vault_path(self, path: str) -> Path:
        """Ensure the path stays within the vault root (prevent traversal).

        Raises:
            ValueError: If the path escapes the vault directory.
        """
        vault = self.vault_path
        resolved = (vault / path).resolve()
        vault_resolved = vault.resolve()
        if not str(resolved).startswith(str(vault_resolved)):
            raise ValueError(
                f"Path traversal detected: {path!r} escapes vault root"
            )
        return resolved

    def is_read_only(self, action: str) -> bool:
        """Return True if the action is read-only."""
        return action in self._READ_ACTIONS
