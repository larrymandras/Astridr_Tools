"""Google Meet tool \u2014 create and manage meetings with auto-generated Meet links.

Reuses the same Google OAuth credentials as GoogleWorkspaceTool
but operates as a separate tool with its own action set.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import structlog

from astridr.engine.config import GoogleAccountConfig
from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

# Map tool actions to the canonical allowed_actions names
_ACTION_PERMISSION_MAP: dict[str, str] = {
    "create_meeting": "create_meet",
    "list_meetings": "list_meets",
    "get_meeting": "get_meet",
}


class GoogleMeetTool(BaseTool):
    """Create and manage Google Meet meetings.

    Uses Google Calendar API with conferenceData to generate Meet links.
    Supports multiple Google accounts with per-account action enforcement.
    """

    name = "google_meet"
    description = "Create and manage Google Meet meetings with auto-generated links"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create_meeting", "list_meetings", "get_meeting"],
                "description": "The operation to perform.",
            },
            "account": {
                "type": "string",
                "description": "Google account alias (defaults to configured default).",
            },
            "title": {
                "type": "string",
                "description": "Meeting title.",
            },
            "start": {
                "type": "string",
                "description": "Start time (ISO 8601).",
            },
            "end": {
                "type": "string",
                "description": "End time (ISO 8601).",
            },
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Email addresses of attendees.",
            },
            "description": {
                "type": "string",
                "description": "Meeting description.",
            },
            "event_id": {
                "type": "string",
                "description": "Calendar event ID for get_meeting.",
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

    _READ_ACTIONS: frozenset[str] = frozenset({"list_meetings", "get_meeting"})

    def __init__(
        self,
        accounts: list[GoogleAccountConfig] | None = None,
        default_account: str = "astridr",
    ) -> None:
        self._accounts: dict[str, GoogleAccountConfig] = {
            acct.alias: acct for acct in (accounts or [])
        }
        self._default_account = default_account
        self._services: dict[str, Any] = {}  # Keyed by account alias

    # ------------------------------------------------------------------
    # Account resolution & enforcement
    # ------------------------------------------------------------------

    def _resolve_account(self, alias: str | None) -> GoogleAccountConfig:
        """Resolve an account alias to its config, falling back to default."""
        target = alias or self._default_account
        if target not in self._accounts:
            raise ValueError(
                f"Unknown account alias: {target!r}. "
                f"Available: {list(self._accounts.keys())}"
            )
        return self._accounts[target]

    def _check_permission(self, account: GoogleAccountConfig, action: str) -> str | None:
        """Return an error message if the action is not allowed, else None."""
        canonical = _ACTION_PERMISSION_MAP.get(action, action)
        if canonical not in account.allowed_actions:
            return (
                f"Action {action!r} is not allowed on account {account.alias!r} "
                f"({account.email}). Allowed: {account.allowed_actions}"
            )
        return None

    # ------------------------------------------------------------------
    # Service client management
    # ------------------------------------------------------------------

    def _get_service(self, account_alias: str) -> Any:
        """Get or lazily build a Calendar API service client for an account."""
        if account_alias in self._services:
            return self._services[account_alias]

        client = self._build_service(account_alias)
        self._services[account_alias] = client
        return client

    def _build_service(self, account_alias: str) -> Any:
        """Build a Google Calendar API service client using OAuth2 credentials."""
        account = self._accounts.get(account_alias)
        if account is None:
            raise ValueError(f"No account config for alias: {account_alias!r}")

        creds_path_str = os.environ.get(account.credentials_ref, "")
        if not creds_path_str:
            raise RuntimeError(
                f"Env var {account.credentials_ref!r} not set for account "
                f"{account_alias!r}. Cannot load credentials."
            )

        creds_path = Path(creds_path_str)
        if not creds_path.exists():
            raise FileNotFoundError(
                f"Credentials file not found: {creds_path} "
                f"(from {account.credentials_ref})"
            )

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds_data = json.loads(creds_path.read_text(encoding="utf-8"))
        credentials = Credentials.from_authorized_user_info(creds_data)

        return build("calendar", "v3", credentials=credentials)

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Route to the appropriate Meet action."""
        action = kwargs.get("action", "")
        account_alias = kwargs.get("account")

        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        # Resolve account and enforce permissions
        try:
            account = self._resolve_account(account_alias)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        perm_error = self._check_permission(account, action)
        if perm_error:
            log.warning("google_meet.action_denied", account=account.alias, action=action)
            return ToolResult(success=False, error=perm_error)

        dispatch = {
            "create_meeting": self._create_meeting,
            "list_meetings": self._list_meetings,
            "get_meeting": self._get_meeting,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(account_alias=account.alias, **kwargs)
        except Exception as exc:
            log.error("google_meet.error", action=action, account=account.alias, error=str(exc))
            return ToolResult(success=False, error=f"Google Meet API error: {exc}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _create_meeting(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Create a calendar event with an auto-generated Meet link."""
        title = kwargs.get("title", "")
        start = kwargs.get("start", "")
        end = kwargs.get("end", "")
        if not title or not start or not end:
            return ToolResult(
                success=False,
                error="title, start, and end are required for create_meeting",
            )

        attendees = kwargs.get("attendees") or []
        description = kwargs.get("description", "")

        event_body: dict[str, Any] = {
            "summary": title,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
            "conferenceData": {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        if attendees:
            event_body["attendees"] = [{"email": e} for e in attendees]
        if description:
            event_body["description"] = description

        service = self._get_service(account_alias)
        event = (
            service.events()
            .insert(calendarId="primary", body=event_body, conferenceDataVersion=1)
            .execute()
        )

        meet_link = event.get("hangoutLink", "")
        log.info(
            "google_meet.created",
            title=title,
            meet_link=meet_link,
            account=account_alias,
        )
        return ToolResult(
            success=True,
            output=f"Created meeting: {title} ({start} - {end})\nMeet link: {meet_link}",
            data={"event": event, "meet_link": meet_link},
        )

    async def _list_meetings(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """List upcoming events that have Meet links."""
        import datetime

        limit = kwargs.get("limit", 10)
        now = datetime.datetime.now(datetime.UTC).isoformat()

        service = self._get_service(account_alias)
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=now,
                maxResults=limit,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        events = events_result.get("items", [])
        # Filter to only events with Meet links
        meet_events = [e for e in events if e.get("hangoutLink")]

        lines = []
        for ev in meet_events:
            start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", ""))
            lines.append(
                f"{start} - {ev.get('summary', '(no title)')}\n  Meet: {ev.get('hangoutLink', '')}"
            )

        log.info("google_meet.list", count=len(meet_events), account=account_alias)
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No upcoming meetings with Meet links.",
            data={"events": meet_events},
        )

    async def _get_meeting(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Get details and Meet link for a specific event."""
        event_id = kwargs.get("event_id", "")
        if not event_id:
            return ToolResult(
                success=False, error="event_id is required for get_meeting"
            )

        service = self._get_service(account_alias)
        event = (
            service.events()
            .get(calendarId="primary", eventId=event_id)
            .execute()
        )

        meet_link = event.get("hangoutLink", "")
        start = event.get("start", {}).get("dateTime", event.get("start", {}).get("date", ""))
        summary = event.get("summary", "(no title)")

        log.info("google_meet.get", event_id=event_id, account=account_alias)
        return ToolResult(
            success=True,
            output=f"{summary} ({start})\nMeet link: {meet_link}" if meet_link else f"{summary} ({start})\nNo Meet link.",
            data={"event": event, "meet_link": meet_link},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_read_only(self, action: str) -> bool:
        """Return True if the action is read-only."""
        return action in self._READ_ACTIONS
