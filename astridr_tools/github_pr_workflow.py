"""Automated PR workflow pipeline — review, prepare, merge.

Provides a three-step pipeline for pull request management:
1. Review: Fetch diff, analyze for issues, return structured review
2. Prepare: Fetch files, suggest improvements with file/line references
3. Merge: Check status, merge via API
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class PRWorkflowTool(BaseTool):
    """Automated PR pipeline: review, prepare, merge."""

    name = "pr_workflow"
    description = "Three-step PR pipeline: review code, prepare changes, merge"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["review", "prepare", "merge"],
                "description": "The PR workflow step to perform.",
            },
            "repo": {
                "type": "string",
                "description": "Repository in owner/repo format.",
            },
            "pr_number": {
                "type": "integer",
                "description": "Pull request number.",
            },
        },
        "required": ["action", "repo", "pr_number"],
        "additionalProperties": False,
    }

    def __init__(self, token: str | None = None) -> None:
        self._token = token or os.environ.get("GITHUB_TOKEN", "")
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create the httpx client."""
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.AsyncClient(
                base_url="https://api.github.com",
                headers=headers,
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # BaseTool.execute
    # ------------------------------------------------------------------

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Dispatch to the requested PR workflow action."""
        action: str = kwargs.get("action", "")
        repo: str = kwargs.get("repo", "")
        pr_number: int | None = kwargs.get("pr_number")

        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")
        if not repo:
            return ToolResult(success=False, error="Missing required parameter: repo")
        if pr_number is None:
            return ToolResult(success=False, error="Missing required parameter: pr_number")

        if "/" not in repo:
            return ToolResult(
                success=False,
                error="repo must be in owner/repo format",
            )

        dispatch = {
            "review": self._review,
            "prepare": self._prepare,
            "merge": self._merge,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(repo=repo, pr_number=pr_number)
        except httpx.HTTPStatusError as exc:
            log.error(
                "pr_workflow.http_error",
                status=exc.response.status_code,
                action=action,
            )
            return ToolResult(
                success=False,
                error=f"GitHub API error {exc.response.status_code}: {exc.response.text}",
            )
        except httpx.HTTPError as exc:
            log.error("pr_workflow.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    async def _review(self, repo: str, pr_number: int) -> ToolResult:
        """Fetch PR diff, analyze it, and return structured review comments."""
        diff = await self._fetch_pr_diff(repo, pr_number)
        info = await self._fetch_pr_info(repo, pr_number)
        analysis = self._analyze_diff(diff)

        title = info.get("title", "")
        author = info.get("user", {}).get("login", "unknown")

        review_lines = [
            f"## PR Review: #{pr_number} — {title}",
            f"**Author:** {author}",
            f"**Files changed:** {analysis['files_changed']}",
            f"**Lines added:** {analysis['lines_added']}",
            f"**Lines removed:** {analysis['lines_removed']}",
            "",
        ]

        if analysis["issues"]:
            review_lines.append("### Issues Found")
            for issue in analysis["issues"]:
                review_lines.append(f"- **{issue['severity']}**: {issue['message']}")
                if issue.get("file"):
                    review_lines.append(f"  File: {issue['file']}")
        else:
            review_lines.append("### No Issues Found")
            review_lines.append("The diff looks clean.")

        output = "\n".join(review_lines)
        log.info(
            "pr_workflow.review_complete",
            repo=repo,
            pr_number=pr_number,
            issues_found=len(analysis["issues"]),
        )
        return ToolResult(
            success=True,
            output=output,
            data={
                "pr_number": pr_number,
                "title": title,
                "author": author,
                "analysis": analysis,
            },
        )

    async def _prepare(self, repo: str, pr_number: int) -> ToolResult:
        """Fetch PR files and suggest specific improvements."""
        info = await self._fetch_pr_info(repo, pr_number)
        files = await self._fetch_pr_files(repo, pr_number)

        title = info.get("title", "")
        suggestions: list[dict[str, Any]] = []

        for file_info in files:
            filename = file_info.get("filename", "")
            patch = file_info.get("patch", "")
            status = file_info.get("status", "")

            file_suggestions = self._suggest_improvements(filename, patch, status)
            suggestions.extend(file_suggestions)

        output_lines = [
            f"## PR Preparation: #{pr_number} — {title}",
            f"**Files changed:** {len(files)}",
            "",
        ]

        if suggestions:
            output_lines.append("### Suggestions")
            for i, suggestion in enumerate(suggestions, 1):
                output_lines.append(
                    f"{i}. **{suggestion['type']}** in `{suggestion['file']}`"
                )
                output_lines.append(f"   {suggestion['message']}")
                if suggestion.get("line"):
                    output_lines.append(f"   Line: {suggestion['line']}")
        else:
            output_lines.append("### No Suggestions")
            output_lines.append("The PR looks ready.")

        output = "\n".join(output_lines)
        log.info(
            "pr_workflow.prepare_complete",
            repo=repo,
            pr_number=pr_number,
            suggestions_count=len(suggestions),
        )
        return ToolResult(
            success=True,
            output=output,
            data={
                "pr_number": pr_number,
                "title": title,
                "files_changed": len(files),
                "suggestions": suggestions,
            },
        )

    async def _merge(self, repo: str, pr_number: int) -> ToolResult:
        """Check PR status and merge via API."""
        info = await self._fetch_pr_info(repo, pr_number)

        # Check if PR is mergeable
        state = info.get("state", "")
        if state != "open":
            return ToolResult(
                success=False,
                error=f"PR #{pr_number} is not open (state: {state})",
            )

        mergeable = info.get("mergeable")
        if mergeable is False:
            return ToolResult(
                success=False,
                error=f"PR #{pr_number} has merge conflicts",
            )

        # Perform merge
        client = self._ensure_client()
        owner, repo_name = repo.split("/", 1)
        resp = await client.put(
            f"/repos/{owner}/{repo_name}/pulls/{pr_number}/merge",
            json={"merge_method": "merge"},
        )
        resp.raise_for_status()
        result = resp.json()

        log.info("pr_workflow.merge_complete", repo=repo, pr_number=pr_number)
        return ToolResult(
            success=True,
            output=f"Merged PR #{pr_number}: {info.get('title', '')}",
            data={
                "pr_number": pr_number,
                "merged": True,
                "sha": result.get("sha", ""),
                "message": result.get("message", ""),
            },
        )

    # ------------------------------------------------------------------
    # API helpers
    # ------------------------------------------------------------------

    async def _fetch_pr_diff(self, repo: str, pr_number: int) -> str:
        """Fetch the PR diff from GitHub API."""
        client = self._ensure_client()
        owner, repo_name = repo.split("/", 1)
        resp = await client.get(
            f"/repos/{owner}/{repo_name}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        resp.raise_for_status()
        return resp.text

    async def _fetch_pr_info(self, repo: str, pr_number: int) -> dict[str, Any]:
        """Fetch PR metadata from GitHub API."""
        client = self._ensure_client()
        owner, repo_name = repo.split("/", 1)
        resp = await client.get(f"/repos/{owner}/{repo_name}/pulls/{pr_number}")
        resp.raise_for_status()
        return resp.json()

    async def _fetch_pr_files(self, repo: str, pr_number: int) -> list[dict[str, Any]]:
        """Fetch the list of files changed in a PR."""
        client = self._ensure_client()
        owner, repo_name = repo.split("/", 1)
        resp = await client.get(f"/repos/{owner}/{repo_name}/pulls/{pr_number}/files")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------

    def _analyze_diff(self, diff: str) -> dict[str, Any]:
        """Parse a unified diff and identify potential issues.

        Returns:
            Dictionary with files_changed, lines_added, lines_removed,
            changed_files, and issues.
        """
        files_changed = 0
        lines_added = 0
        lines_removed = 0
        changed_files: list[str] = []
        issues: list[dict[str, str]] = []
        current_file: str = ""

        for line in diff.split("\n"):
            # Track file changes
            if line.startswith("diff --git"):
                files_changed += 1
                match = re.search(r"b/(.+)$", line)
                if match:
                    current_file = match.group(1)
                    changed_files.append(current_file)
            elif line.startswith("+") and not line.startswith("+++"):
                lines_added += 1
                added_content = line[1:]

                # Detect hardcoded secrets / tokens
                secret_patterns = [
                    r"(?:password|secret|token|api_key|apikey)\s*=\s*['\"][^'\"]+['\"]",
                    r"(?:AWS_SECRET|PRIVATE_KEY)",
                ]
                for pattern in secret_patterns:
                    if re.search(pattern, added_content, re.IGNORECASE):
                        issues.append({
                            "severity": "HIGH",
                            "message": "Possible hardcoded secret or credential",
                            "file": current_file,
                        })

                # Detect TODO/FIXME comments
                if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", added_content):
                    issues.append({
                        "severity": "LOW",
                        "message": "TODO/FIXME comment found in added code",
                        "file": current_file,
                    })

                # Detect missing error handling (bare except)
                if re.search(r"except\s*:", added_content):
                    issues.append({
                        "severity": "MEDIUM",
                        "message": "Bare except clause — consider catching specific exceptions",
                        "file": current_file,
                    })

            elif line.startswith("-") and not line.startswith("---"):
                lines_removed += 1

        return {
            "files_changed": files_changed,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "changed_files": changed_files,
            "issues": issues,
        }

    def _suggest_improvements(
        self,
        filename: str,
        patch: str,
        status: str,
    ) -> list[dict[str, Any]]:
        """Suggest improvements for a single file's changes.

        Returns:
            List of suggestion dicts with type, file, message, and optional line.
        """
        suggestions: list[dict[str, Any]] = []

        if not patch:
            return suggestions

        line_number = 0
        for line in patch.split("\n"):
            # Track line numbers from hunk headers
            hunk_match = re.match(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)", line)
            if hunk_match:
                line_number = int(hunk_match.group(1))
                continue

            if line.startswith("+") and not line.startswith("+++"):
                added_content = line[1:]
                line_number += 1

                # Large function detection (very long lines)
                if len(added_content) > 120:
                    suggestions.append({
                        "type": "style",
                        "file": filename,
                        "message": "Line exceeds 120 characters — consider breaking it up",
                        "line": line_number,
                    })

                # Print statement detection in Python files
                if filename.endswith(".py") and re.search(
                    r"\bprint\s*\(", added_content
                ):
                    suggestions.append({
                        "type": "quality",
                        "file": filename,
                        "message": "print() statement found — consider using a logger",
                        "line": line_number,
                    })

                # Detect debug imports
                if re.search(r"import\s+pdb|import\s+ipdb|breakpoint\(\)", added_content):
                    suggestions.append({
                        "type": "quality",
                        "file": filename,
                        "message": "Debug import/breakpoint found — remove before merging",
                        "line": line_number,
                    })
            elif line.startswith("-") and not line.startswith("---"):
                pass  # removed line — don't increment
            else:
                line_number += 1

        return suggestions
