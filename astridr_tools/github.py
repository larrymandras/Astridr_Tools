"""GitHub integration tool — read-only + write operations via REST API.

Phase 1: read-only operations, create_issue, add_comment.
Phase 2: create_pr, review_pr, merge_pr, list_branches, create_branch, trigger_workflow.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class GitHubTool(BaseTool):
    """Interact with GitHub: repos, issues, PRs, branches, workflows.

    Phase 1: read-only operations, create_issue, add_comment.
    Phase 2: create_pr, review_pr, merge_pr, list_branches, create_branch,
             trigger_workflow.
    """

    name = "github"
    description = "Interact with GitHub: repos, issues, PRs, code search"
    approval_tier = "supervised"  # writes need approval; reads are safe

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_repos",
                    "get_repo",
                    "list_issues",
                    "create_issue",
                    "add_comment",
                    "list_prs",
                    "get_file",
                    "search_code",
                    "create_pr",
                    "review_pr",
                    "merge_pr",
                    "list_branches",
                    "create_branch",
                    "trigger_workflow",
                ],
                "description": "The GitHub operation to perform.",
            },
            "owner": {
                "type": "string",
                "description": "Repository owner (user or organisation).",
            },
            "repo": {
                "type": "string",
                "description": "Repository name.",
            },
            "issue_number": {
                "type": "integer",
                "description": "Issue or PR number.",
            },
            "title": {
                "type": "string",
                "description": "Title for a new issue.",
            },
            "body": {
                "type": "string",
                "description": "Body text for an issue or comment.",
            },
            "path": {
                "type": "string",
                "description": "File path inside the repository.",
            },
            "ref": {
                "type": "string",
                "description": "Git ref (branch, tag, or SHA). Defaults to the repo default branch.",
            },
            "query": {
                "type": "string",
                "description": "Search query string for code search.",
            },
            "per_page": {
                "type": "integer",
                "description": "Results per page (max 100).",
                "default": 30,
            },
            "page": {
                "type": "integer",
                "description": "Page number for paginated results.",
                "default": 1,
            },
            "state": {
                "type": "string",
                "enum": ["open", "closed", "all"],
                "description": "Filter issues/PRs by state.",
                "default": "open",
            },
            "head": {
                "type": "string",
                "description": "Source branch for PR creation (head).",
            },
            "base": {
                "type": "string",
                "description": "Target branch for PR creation (base). Defaults to repo default.",
            },
            "event": {
                "type": "string",
                "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"],
                "description": "PR review event type.",
            },
            "merge_method": {
                "type": "string",
                "enum": ["merge", "squash", "rebase"],
                "description": "Merge method for merge_pr.",
                "default": "merge",
            },
            "branch": {
                "type": "string",
                "description": "Branch name for create_branch.",
            },
            "sha": {
                "type": "string",
                "description": "SHA to branch from for create_branch.",
            },
            "workflow_id": {
                "type": "string",
                "description": "Workflow ID or filename for trigger_workflow.",
            },
            "inputs": {
                "type": "object",
                "description": "Input parameters for trigger_workflow.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    # Actions that only read data (no approval needed).
    _READ_ACTIONS: frozenset[str] = frozenset(
        {
            "list_repos",
            "get_repo",
            "list_issues",
            "list_prs",
            "get_file",
            "search_code",
            "list_branches",
        }
    )

    def __init__(self, token: str | None = None) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN", "")
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Client lifecycle
    # ------------------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create the httpx client so the tool can be instantiated
        without an event loop and re-used across calls."""
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
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
        """Dispatch to the requested GitHub action."""
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        dispatch = {
            "list_repos": self._list_repos,
            "get_repo": self._get_repo,
            "list_issues": self._list_issues,
            "create_issue": self._create_issue,
            "add_comment": self._add_comment,
            "list_prs": self._list_prs,
            "get_file": self._get_file,
            "search_code": self._search_code,
            # Phase 2 actions
            "create_pr": self._create_pr,
            "review_pr": self._review_pr,
            "merge_pr": self._merge_pr,
            "list_branches": self._list_branches,
            "create_branch": self._create_branch,
            "trigger_workflow": self._trigger_workflow,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("github.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"GitHub API error {exc.response.status_code}: {exc.response.text}",
            )
        except httpx.HTTPError as exc:
            log.error("github.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def _list_repos(self, **kwargs: Any) -> ToolResult:
        """List repositories for the authenticated user or a given owner."""
        client = self._ensure_client()
        owner = kwargs.get("owner")
        per_page = kwargs.get("per_page", 30)
        page = kwargs.get("page", 1)

        if owner:
            url = f"/users/{owner}/repos"
        else:
            url = "/user/repos"

        resp = await client.get(url, params={"per_page": per_page, "page": page})
        resp.raise_for_status()
        repos = resp.json()
        names = [r["full_name"] for r in repos]
        return ToolResult(success=True, output="\n".join(names), data={"repos": repos})

    async def _get_repo(self, **kwargs: Any) -> ToolResult:
        """Get details for a single repository."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        if not owner or not repo:
            return ToolResult(success=False, error="owner and repo are required for get_repo")

        resp = await client.get(f"/repos/{owner}/{repo}")
        resp.raise_for_status()
        data = resp.json()
        return ToolResult(
            success=True,
            output=f"{data['full_name']} - {data.get('description', '')}",
            data={"repo": data},
        )

    async def _list_issues(self, **kwargs: Any) -> ToolResult:
        """List issues for a repository."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        if not owner or not repo:
            return ToolResult(success=False, error="owner and repo are required for list_issues")

        params: dict[str, Any] = {
            "state": kwargs.get("state", "open"),
            "per_page": kwargs.get("per_page", 30),
            "page": kwargs.get("page", 1),
        }
        resp = await client.get(f"/repos/{owner}/{repo}/issues", params=params)
        resp.raise_for_status()
        issues = resp.json()
        lines = [f"#{i['number']} {i['title']}" for i in issues]
        return ToolResult(success=True, output="\n".join(lines), data={"issues": issues})

    async def _list_prs(self, **kwargs: Any) -> ToolResult:
        """List pull requests for a repository."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        if not owner or not repo:
            return ToolResult(success=False, error="owner and repo are required for list_prs")

        params: dict[str, Any] = {
            "state": kwargs.get("state", "open"),
            "per_page": kwargs.get("per_page", 30),
            "page": kwargs.get("page", 1),
        }
        resp = await client.get(f"/repos/{owner}/{repo}/pulls", params=params)
        resp.raise_for_status()
        prs = resp.json()
        lines = [f"#{p['number']} {p['title']}" for p in prs]
        return ToolResult(success=True, output="\n".join(lines), data={"prs": prs})

    async def _get_file(self, **kwargs: Any) -> ToolResult:
        """Get file contents from a repository."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        path = kwargs.get("path", "")
        if not owner or not repo or not path:
            return ToolResult(
                success=False, error="owner, repo, and path are required for get_file"
            )

        params: dict[str, str] = {}
        ref = kwargs.get("ref")
        if ref:
            params["ref"] = ref

        resp = await client.get(f"/repos/{owner}/{repo}/contents/{path}", params=params)
        resp.raise_for_status()
        data = resp.json()
        # GitHub returns base64-encoded content for files
        content = ""
        if data.get("encoding") == "base64" and data.get("content"):
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")

        return ToolResult(success=True, output=content, data={"file": data})

    async def _search_code(self, **kwargs: Any) -> ToolResult:
        """Search code across GitHub repositories."""
        client = self._ensure_client()
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for search_code")

        params: dict[str, Any] = {
            "q": query,
            "per_page": kwargs.get("per_page", 30),
            "page": kwargs.get("page", 1),
        }
        resp = await client.get("/search/code", params=params)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        lines = [f"{it['repository']['full_name']}: {it['path']}" for it in items]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            data={"total_count": data.get("total_count", 0), "items": items},
        )

    # ------------------------------------------------------------------
    # Write operations (supervised — require approval)
    # ------------------------------------------------------------------

    async def _create_issue(self, **kwargs: Any) -> ToolResult:
        """Create a new issue in a repository."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        title = kwargs.get("title", "")
        if not owner or not repo or not title:
            return ToolResult(
                success=False, error="owner, repo, and title are required for create_issue"
            )

        body = kwargs.get("body", "")
        payload: dict[str, str] = {"title": title}
        if body:
            payload["body"] = body

        resp = await client.post(f"/repos/{owner}/{repo}/issues", json=payload)
        resp.raise_for_status()
        issue = resp.json()
        log.info("github.issue_created", owner=owner, repo=repo, number=issue["number"])
        return ToolResult(
            success=True,
            output=f"Created issue #{issue['number']}: {issue['title']}",
            data={"issue": issue},
        )

    async def _add_comment(self, **kwargs: Any) -> ToolResult:
        """Add a comment to an existing issue."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        issue_number = kwargs.get("issue_number")
        body = kwargs.get("body", "")
        if not owner or not repo or issue_number is None or not body:
            return ToolResult(
                success=False,
                error="owner, repo, issue_number, and body are required for add_comment",
            )

        resp = await client.post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        resp.raise_for_status()
        comment = resp.json()
        log.info(
            "github.comment_added",
            owner=owner,
            repo=repo,
            issue_number=issue_number,
        )
        return ToolResult(
            success=True,
            output=f"Added comment to #{issue_number}",
            data={"comment": comment},
        )

    # ------------------------------------------------------------------
    # Phase 2 — Branch & PR operations
    # ------------------------------------------------------------------

    async def _list_branches(self, **kwargs: Any) -> ToolResult:
        """List branches for a repository."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        if not owner or not repo:
            return ToolResult(
                success=False, error="owner and repo are required for list_branches"
            )

        params: dict[str, Any] = {
            "per_page": kwargs.get("per_page", 30),
            "page": kwargs.get("page", 1),
        }
        resp = await client.get(f"/repos/{owner}/{repo}/branches", params=params)
        resp.raise_for_status()
        branches = resp.json()
        names = [b["name"] for b in branches]
        return ToolResult(
            success=True,
            output="\n".join(names),
            data={"branches": branches},
        )

    async def _create_branch(self, **kwargs: Any) -> ToolResult:
        """Create a new branch from a given SHA."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        branch = kwargs.get("branch", "")
        sha = kwargs.get("sha", "")
        if not owner or not repo or not branch or not sha:
            return ToolResult(
                success=False,
                error="owner, repo, branch, and sha are required for create_branch",
            )

        payload = {"ref": f"refs/heads/{branch}", "sha": sha}
        resp = await client.post(f"/repos/{owner}/{repo}/git/refs", json=payload)
        resp.raise_for_status()
        ref_data = resp.json()
        log.info("github.branch_created", owner=owner, repo=repo, branch=branch)
        return ToolResult(
            success=True,
            output=f"Created branch: {branch}",
            data={"ref": ref_data},
        )

    async def _create_pr(self, **kwargs: Any) -> ToolResult:
        """Create a pull request."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        title = kwargs.get("title", "")
        head = kwargs.get("head", "")
        base = kwargs.get("base", "")
        if not owner or not repo or not title or not head:
            return ToolResult(
                success=False,
                error="owner, repo, title, and head are required for create_pr",
            )

        payload: dict[str, str] = {"title": title, "head": head}
        if base:
            payload["base"] = base
        else:
            # Default to repo's default branch — fetch it first
            repo_resp = await client.get(f"/repos/{owner}/{repo}")
            repo_resp.raise_for_status()
            payload["base"] = repo_resp.json().get("default_branch", "main")

        body = kwargs.get("body", "")
        if body:
            payload["body"] = body

        resp = await client.post(f"/repos/{owner}/{repo}/pulls", json=payload)
        resp.raise_for_status()
        pr = resp.json()
        log.info("github.pr_created", owner=owner, repo=repo, number=pr["number"])
        return ToolResult(
            success=True,
            output=f"Created PR #{pr['number']}: {pr['title']}",
            data={"pr": pr},
        )

    async def _review_pr(self, **kwargs: Any) -> ToolResult:
        """Add a review to a pull request."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        issue_number = kwargs.get("issue_number")
        event = kwargs.get("event", "")
        body = kwargs.get("body", "")
        if not owner or not repo or issue_number is None or not event:
            return ToolResult(
                success=False,
                error="owner, repo, issue_number, and event are required for review_pr",
            )

        valid_events = {"APPROVE", "REQUEST_CHANGES", "COMMENT"}
        if event not in valid_events:
            return ToolResult(
                success=False,
                error=f"event must be one of {valid_events}, got: {event}",
            )

        payload: dict[str, str] = {"event": event}
        if body:
            payload["body"] = body

        resp = await client.post(
            f"/repos/{owner}/{repo}/pulls/{issue_number}/reviews",
            json=payload,
        )
        resp.raise_for_status()
        review = resp.json()
        log.info(
            "github.pr_reviewed",
            owner=owner,
            repo=repo,
            pr_number=issue_number,
            review_event=event,
        )
        return ToolResult(
            success=True,
            output=f"Added {event} review to PR #{issue_number}",
            data={"review": review},
        )

    async def _merge_pr(self, **kwargs: Any) -> ToolResult:
        """Merge a pull request."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        issue_number = kwargs.get("issue_number")
        if not owner or not repo or issue_number is None:
            return ToolResult(
                success=False,
                error="owner, repo, and issue_number are required for merge_pr",
            )

        merge_method = kwargs.get("merge_method", "merge")
        valid_methods = {"merge", "squash", "rebase"}
        if merge_method not in valid_methods:
            return ToolResult(
                success=False,
                error=f"merge_method must be one of {valid_methods}, got: {merge_method}",
            )

        payload: dict[str, str] = {"merge_method": merge_method}

        resp = await client.put(
            f"/repos/{owner}/{repo}/pulls/{issue_number}/merge",
            json=payload,
        )
        resp.raise_for_status()
        result = resp.json()
        log.info(
            "github.pr_merged",
            owner=owner,
            repo=repo,
            pr_number=issue_number,
            method=merge_method,
        )
        return ToolResult(
            success=True,
            output=f"Merged PR #{issue_number} via {merge_method}",
            data={"merge": result},
        )

    # ------------------------------------------------------------------
    # Phase 2 — Workflow operations
    # ------------------------------------------------------------------

    async def _trigger_workflow(self, **kwargs: Any) -> ToolResult:
        """Trigger a GitHub Actions workflow dispatch event."""
        client = self._ensure_client()
        owner = kwargs.get("owner", "")
        repo = kwargs.get("repo", "")
        workflow_id = kwargs.get("workflow_id", "")
        ref = kwargs.get("ref", "")
        if not owner or not repo or not workflow_id:
            return ToolResult(
                success=False,
                error="owner, repo, and workflow_id are required for trigger_workflow",
            )

        if not ref:
            # Default to repo's default branch
            repo_resp = await client.get(f"/repos/{owner}/{repo}")
            repo_resp.raise_for_status()
            ref = repo_resp.json().get("default_branch", "main")

        payload: dict[str, Any] = {"ref": ref}
        inputs = kwargs.get("inputs")
        if inputs and isinstance(inputs, dict):
            payload["inputs"] = inputs

        resp = await client.post(
            f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches",
            json=payload,
        )
        resp.raise_for_status()
        log.info(
            "github.workflow_triggered",
            owner=owner,
            repo=repo,
            workflow_id=workflow_id,
            ref=ref,
        )
        return ToolResult(
            success=True,
            output=f"Triggered workflow {workflow_id} on {ref}",
            data={"workflow_id": workflow_id, "ref": ref},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_read_only(self, action: str) -> bool:
        """Return True if the given action is read-only (no approval needed)."""
        return action in self._READ_ACTIONS
