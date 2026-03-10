"""Google Workspace integration tool — Gmail, Calendar, Drive.

Supports multiple Google accounts with per-account action enforcement
and HTML email signatures with inline avatar.
"""

from __future__ import annotations

import base64
import json
import os
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import structlog

from astridr.engine.config import GoogleAccountConfig
from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

# Mapping from action names to Google API service/version
_SERVICE_MAP: dict[str, tuple[str, str]] = {
    "gmail": ("gmail", "v1"),
    "calendar": ("calendar", "v3"),
    "drive": ("drive", "v3"),
}

# Map tool actions to the canonical allowed_actions names
_ACTION_PERMISSION_MAP: dict[str, str] = {
    # Gmail
    "search": "search",
    "read": "read",
    "send": "send",
    "draft": "draft",
    # Calendar
    "list": "list_events",
    "list_events": "list_events",
    "create": "create_event",
    "create_event": "create_event",
    # Drive
    "create_file": "create_file",
}

_SIGNATURE_HTML = """\
<br><br>
<table cellpadding="0" cellspacing="0" style="font-family:Arial,sans-serif;font-size:13px;color:#555;">
  <tr>
    <td style="padding-right:12px;vertical-align:middle;">
      <img src="cid:astridr-avatar" alt="\u00c1str\u00ed\u00f0r" width="48" height="48"
           style="border-radius:50%;display:block;" />
    </td>
    <td style="vertical-align:middle;">
      <strong style="color:#222;">\u00c1str\u00ed\u00f0r</strong> \u26a1<br>
      <span style="font-size:11px;color:#888;">AI Assistant</span>
    </td>
  </tr>
</table>"""

_SIGNATURE_PLAIN = "\n\n\u2014 \u00c1str\u00ed\u00f0r \u26a1"


class GoogleWorkspaceTool(BaseTool):
    """Interact with Google Workspace (Gmail, Calendar, Drive).

    Supports multiple accounts with per-account action enforcement.
    Outgoing emails include an HTML signature with inline avatar.
    """

    name = "google"
    description = "Access Gmail, Google Calendar, and Google Drive"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "enum": ["gmail", "calendar", "drive"],
                "description": "Which Google Workspace service to use.",
            },
            "action": {
                "type": "string",
                "description": "The operation to perform within the service.",
            },
            "account": {
                "type": "string",
                "description": (
                    "Account alias to use (e.g. 'astridr', 'personal', 'business'). "
                    "Defaults to the configured default account."
                ),
            },
            "query": {
                "type": "string",
                "description": "Search query (Gmail search, Drive search).",
            },
            "message_id": {
                "type": "string",
                "description": "Gmail message ID for read operations.",
            },
            "to": {
                "type": "string",
                "description": "Recipient email address.",
            },
            "subject": {
                "type": "string",
                "description": "Email subject line.",
            },
            "body": {
                "type": "string",
                "description": "Email body or event description.",
            },
            "event_title": {
                "type": "string",
                "description": "Calendar event title.",
            },
            "event_start": {
                "type": "string",
                "description": "Event start time (ISO 8601).",
            },
            "event_end": {
                "type": "string",
                "description": "Event end time (ISO 8601).",
            },
            "file_id": {
                "type": "string",
                "description": "Google Drive file ID.",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Maximum number of results to return.",
            },
        },
        "required": ["service", "action"],
        "additionalProperties": False,
    }

    # Actions that only read data (no approval needed).
    _READ_ACTIONS: frozenset[str] = frozenset(
        {"search", "read", "list", "list_events"}
    )

    def __init__(
        self,
        accounts: list[GoogleAccountConfig] | None = None,
        default_account: str = "astridr",
        avatar_path: Path | str | None = None,
    ) -> None:
        self._accounts: dict[str, GoogleAccountConfig] = {
            acct.alias: acct for acct in (accounts or [])
        }
        self._default_account = default_account
        self._avatar_path = Path(avatar_path) if avatar_path else None
        self._services: dict[str, Any] = {}  # Keyed by "alias:service_name"

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

    def _get_service(self, service_name: str, account_alias: str) -> Any:
        """Get or lazily build a Google API service client for an account."""
        cache_key = f"{account_alias}:{service_name}"
        if cache_key in self._services:
            return self._services[cache_key]

        client = self._build_service(service_name, account_alias)
        self._services[cache_key] = client
        return client

    def _build_service(self, service_name: str, account_alias: str) -> Any:
        """Build a Google API service client using OAuth2 credentials."""
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

        api_service, api_version = _SERVICE_MAP[service_name]
        creds_data = json.loads(creds_path.read_text(encoding="utf-8"))
        credentials = Credentials.from_authorized_user_info(creds_data)

        return build(api_service, api_version, credentials=credentials)

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Route to the appropriate service handler."""
        service = kwargs.get("service", "")
        action = kwargs.get("action", "")
        account_alias = kwargs.get("account")

        if not service:
            return ToolResult(success=False, error="Missing required parameter: service")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        # Resolve account and enforce permissions
        try:
            account = self._resolve_account(account_alias)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        perm_error = self._check_permission(account, action)
        if perm_error:
            log.warning("google.action_denied", account=account.alias, action=action)
            return ToolResult(success=False, error=perm_error)

        dispatch = {
            "gmail": self._handle_gmail,
            "calendar": self._handle_calendar,
            "drive": self._handle_drive,
        }

        handler = dispatch.get(service)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown service: {service}")

        try:
            return await handler(account_alias=account.alias, **kwargs)
        except NotImplementedError as exc:
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:
            log.error(
                "google.error",
                service=service,
                action=action,
                account=account.alias,
                error=str(exc),
            )
            return ToolResult(success=False, error=f"Google API error: {exc}")

    # ------------------------------------------------------------------
    # HTML email builder
    # ------------------------------------------------------------------

    def _build_gmail_message(
        self, to: str, subject: str, body: str
    ) -> MIMEMultipart:
        """Build a multipart MIME message with HTML signature and inline avatar.

        Structure (avatar inline, not as attachment):
          multipart/related
            multipart/alternative
              text/plain
              text/html  (references cid:astridr-avatar)
            image/png    (Content-ID: <astridr-avatar>)
        """
        # Plain + HTML alternatives
        alt = MIMEMultipart("alternative")

        plain_text = body + _SIGNATURE_PLAIN
        alt.attach(MIMEText(plain_text, "plain", "utf-8"))

        body_html = body.replace("\n", "<br>\n")
        html_content = (
            f'<div style="font-family:Arial,sans-serif;font-size:14px;color:#222;">'
            f"{body_html}"
            f"{_SIGNATURE_HTML}"
            f"</div>"
        )
        alt.attach(MIMEText(html_content, "html", "utf-8"))

        # Wrap in multipart/related so the inline image is tied to the HTML
        if self._avatar_path and self._avatar_path.exists():
            related = MIMEMultipart("related")
            related["to"] = to
            related["subject"] = subject
            related.attach(alt)

            img_data = self._avatar_path.read_bytes()
            img = MIMEImage(img_data, _subtype="png")
            img.add_header("Content-ID", "<astridr-avatar>")
            img.add_header("Content-Disposition", "inline", filename="avatar.png")
            related.attach(img)

            return related

        # No avatar — just plain/HTML alternative
        alt["to"] = to
        alt["subject"] = subject
        return alt

    # ------------------------------------------------------------------
    # Gmail
    # ------------------------------------------------------------------

    async def _handle_gmail(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Dispatch Gmail actions."""
        action = kwargs.get("action", "")
        gmail_dispatch = {
            "search": self._gmail_search,
            "read": self._gmail_read,
            "send": self._gmail_send,
            "draft": self._gmail_draft,
        }
        handler = gmail_dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown Gmail action: {action}")
        return await handler(account_alias=account_alias, **kwargs)

    async def _gmail_search(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Search Gmail messages."""
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(success=False, error="query is required for gmail search")
        limit = kwargs.get("limit", 10)

        service = self._get_service("gmail", account_alias)
        results = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=limit)
            .execute()
        )
        messages = results.get("messages", [])

        lines = [f"ID: {m['id']}" for m in messages]
        log.info("google.gmail.search", query=query, count=len(messages), account=account_alias)
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No messages found.",
            data={"messages": messages},
        )

    async def _gmail_read(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Read a single Gmail message by ID."""
        message_id = kwargs.get("message_id", "")
        if not message_id:
            return ToolResult(
                success=False, error="message_id is required for gmail read"
            )

        service = self._get_service("gmail", account_alias)
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

        snippet = msg.get("snippet", "")
        headers = {
            h["name"]: h["value"]
            for h in msg.get("payload", {}).get("headers", [])
            if h["name"] in ("From", "To", "Subject", "Date")
        }
        output = (
            f"From: {headers.get('From', 'Unknown')}\n"
            f"To: {headers.get('To', 'Unknown')}\n"
            f"Subject: {headers.get('Subject', '(no subject)')}\n"
            f"Date: {headers.get('Date', 'Unknown')}\n\n"
            f"{snippet}"
        )

        log.info("google.gmail.read", message_id=message_id, account=account_alias)
        return ToolResult(success=True, output=output, data={"message": msg})

    async def _gmail_send(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Send an email with HTML signature and inline avatar."""
        to = kwargs.get("to", "")
        subject = kwargs.get("subject", "")
        body = kwargs.get("body", "")
        if not to or not subject:
            return ToolResult(
                success=False, error="to and subject are required for gmail send"
            )

        message = self._build_gmail_message(to, subject, body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        service = self._get_service("gmail", account_alias)
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )

        log.info("google.gmail.sent", to=to, subject=subject, account=account_alias)
        return ToolResult(
            success=True,
            output=f"Email sent to {to}: {subject} (via {account_alias})",
            data={"message": sent},
        )

    async def _gmail_draft(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Create an email draft with HTML signature and inline avatar."""
        to = kwargs.get("to", "")
        subject = kwargs.get("subject", "")
        body = kwargs.get("body", "")
        if not to or not subject:
            return ToolResult(
                success=False, error="to and subject are required for gmail draft"
            )

        message = self._build_gmail_message(to, subject, body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        service = self._get_service("gmail", account_alias)
        draft = (
            service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw}})
            .execute()
        )

        log.info("google.gmail.draft_created", to=to, subject=subject, account=account_alias)
        return ToolResult(
            success=True,
            output=f"Draft created for {to}: {subject} (via {account_alias})",
            data={"draft": draft},
        )

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------

    async def _handle_calendar(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Dispatch Calendar actions."""
        action = kwargs.get("action", "")
        cal_dispatch = {
            "list": self._calendar_list,
            "list_events": self._calendar_list,
            "create": self._calendar_create,
            "create_event": self._calendar_create,
        }
        handler = cal_dispatch.get(action)
        if handler is None:
            return ToolResult(
                success=False, error=f"Unknown Calendar action: {action}"
            )
        return await handler(account_alias=account_alias, **kwargs)

    async def _calendar_list(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """List upcoming calendar events."""
        limit = kwargs.get("limit", 10)

        service = self._get_service("calendar", account_alias)
        import datetime

        now = datetime.datetime.now(datetime.UTC).isoformat()
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

        lines = []
        for ev in events:
            start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", ""))
            lines.append(f"{start} - {ev.get('summary', '(no title)')}")

        log.info("google.calendar.list", count=len(events), account=account_alias)
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No upcoming events.",
            data={"events": events},
        )

    async def _calendar_create(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Create a calendar event."""
        title = kwargs.get("event_title", "")
        start = kwargs.get("event_start", "")
        end = kwargs.get("event_end", "")
        if not title or not start or not end:
            return ToolResult(
                success=False,
                error="event_title, event_start, and event_end are required for calendar create",
            )

        service = self._get_service("calendar", account_alias)
        event_body = {
            "summary": title,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }
        if kwargs.get("body"):
            event_body["description"] = kwargs["body"]

        event = (
            service.events()
            .insert(calendarId="primary", body=event_body)
            .execute()
        )

        log.info("google.calendar.created", title=title, start=start, account=account_alias)
        return ToolResult(
            success=True,
            output=f"Created event: {title} ({start} - {end}) (via {account_alias})",
            data={"event": event},
        )

    # ------------------------------------------------------------------
    # Drive
    # ------------------------------------------------------------------

    async def _handle_drive(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Dispatch Drive actions."""
        action = kwargs.get("action", "")
        drive_dispatch = {
            "list": self._drive_list,
            "read": self._drive_read,
            "create_file": self._drive_create_file,
        }
        handler = drive_dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown Drive action: {action}")
        return await handler(account_alias=account_alias, **kwargs)

    async def _drive_list(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """List files in Google Drive, optionally filtered by query."""
        query = kwargs.get("query")
        limit = kwargs.get("limit", 10)

        service = self._get_service("drive", account_alias)
        params: dict[str, Any] = {"pageSize": limit, "fields": "files(id, name, mimeType)"}
        if query:
            params["q"] = query

        results = service.files().list(**params).execute()
        files = results.get("files", [])

        lines = [f"{f['name']} (ID: {f['id']})" for f in files]
        log.info("google.drive.list", count=len(files), account=account_alias)
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No files found.",
            data={"files": files},
        )

    async def _drive_read(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Read/download a file from Google Drive."""
        file_id = kwargs.get("file_id", "")
        if not file_id:
            return ToolResult(
                success=False, error="file_id is required for drive read"
            )

        service = self._get_service("drive", account_alias)
        meta = service.files().get(fileId=file_id, fields="id, name, mimeType").execute()

        mime = meta.get("mimeType", "")
        if mime.startswith("application/vnd.google-apps."):
            content_bytes = (
                service.files()
                .export(fileId=file_id, mimeType="text/plain")
                .execute()
            )
            content = content_bytes.decode("utf-8", errors="replace") if isinstance(content_bytes, bytes) else str(content_bytes)
        else:
            content_bytes = service.files().get_media(fileId=file_id).execute()
            content = content_bytes.decode("utf-8", errors="replace") if isinstance(content_bytes, bytes) else str(content_bytes)

        log.info("google.drive.read", file_id=file_id, name=meta.get("name"), account=account_alias)
        return ToolResult(
            success=True,
            output=content,
            data={"file": meta},
        )

    async def _drive_create_file(self, *, account_alias: str, **kwargs: Any) -> ToolResult:
        """Create a file in Google Drive and auto-share if configured."""
        from googleapiclient.http import MediaInMemoryUpload

        name = kwargs.get("subject") or kwargs.get("event_title") or ""
        body_content = kwargs.get("body", "")
        if not name:
            return ToolResult(
                success=False,
                error="subject is required for drive create_file (used as filename)",
            )

        service = self._get_service("drive", account_alias)

        file_metadata: dict[str, Any] = {"name": name}

        media = MediaInMemoryUpload(
            body_content.encode("utf-8"),
            mimetype="text/plain",
            resumable=False,
        )

        created = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id, name, webViewLink")
            .execute()
        )

        file_id = created["id"]

        # Auto-share with configured emails
        account = self._accounts.get(account_alias)
        shared_with: list[str] = []
        if account and account.auto_share_with:
            for email in account.auto_share_with:
                try:
                    service.permissions().create(
                        fileId=file_id,
                        body={"type": "user", "role": "writer", "emailAddress": email},
                        sendNotificationEmail=False,
                    ).execute()
                    shared_with.append(email)
                except Exception as exc:
                    log.warning(
                        "google.drive.share_failed",
                        file_id=file_id,
                        email=email,
                        error=str(exc),
                    )

        link = created.get("webViewLink", "")
        share_msg = f" (shared with {', '.join(shared_with)})" if shared_with else ""
        log.info(
            "google.drive.created",
            file_id=file_id,
            name=created["name"],
            account=account_alias,
            shared_with=shared_with,
        )
        return ToolResult(
            success=True,
            output=f"Created file: {created['name']} (ID: {file_id}){f' \u2014 {link}' if link else ''}{share_msg}",
            data={"file": created, "shared_with": shared_with},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_read_only(self, action: str) -> bool:
        """Return True if the action is read-only."""
        return action in self._READ_ACTIONS
