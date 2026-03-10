"""Slack tool \u2014 proactive Slack workspace interaction.

Distinct from SlackChannel (which listens for incoming messages).
This tool lets agents post, read, search, and manage Slack channels.
"""

from __future__ import annotations

from typing import Any

import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class SlackTool(BaseTool):
    """Proactively interact with Slack: post messages, read channels, search.

    Uses the Slack Web API via AsyncWebClient (not Socket Mode).
    """

    name = "slack_tool"
    description = "Post messages, read channels, search, and manage Slack workspace"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "post_message",
                    "read_channel",
                    "search_messages",
                    "list_channels",
                    "set_topic",
                    "list_users",
                ],
                "description": "The operation to perform.",
            },
            "channel": {
                "type": "string",
                "description": "Channel ID or name.",
            },
            "text": {
                "type": "string",
                "description": "Message text.",
            },
            "query": {
                "type": "string",
                "description": "Search query.",
            },
            "topic": {
                "type": "string",
                "description": "New channel topic.",
            },
            "thread_ts": {
                "type": "string",
                "description": "Thread timestamp for replies.",
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "Maximum number of results to return.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset(
        {"read_channel", "search_messages", "list_channels", "list_users"}
    )

    def __init__(self, bot_token: str) -> None:
        self._bot_token = bot_token
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazily create the Slack AsyncWebClient."""
        if self._client is None:
            from slack_sdk.web.async_client import AsyncWebClient

            self._client = AsyncWebClient(token=self._bot_token)
        return self._client

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Route to the appropriate Slack action."""
        action = kwargs.get("action", "")

        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        dispatch = {
            "post_message": self._post_message,
            "read_channel": self._read_channel,
            "search_messages": self._search_messages,
            "list_channels": self._list_channels,
            "set_topic": self._set_topic,
            "list_users": self._list_users,
        }

        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except Exception as exc:
            log.error("slack_tool.error", action=action, error=str(exc))
            return ToolResult(success=False, error=f"Slack API error: {exc}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def _post_message(self, **kwargs: Any) -> ToolResult:
        """Post a message to a Slack channel."""
        channel = kwargs.get("channel", "")
        text = kwargs.get("text", "")
        if not channel or not text:
            return ToolResult(
                success=False, error="channel and text are required for post_message"
            )

        client = self._get_client()
        api_kwargs: dict[str, Any] = {"channel": channel, "text": text}
        if kwargs.get("thread_ts"):
            api_kwargs["thread_ts"] = kwargs["thread_ts"]

        response = await client.chat_postMessage(**api_kwargs)

        log.info("slack_tool.posted", channel=channel, thread_ts=kwargs.get("thread_ts"))
        return ToolResult(
            success=True,
            output=f"Message posted to {channel}",
            data={"response": response.data},
        )

    async def _read_channel(self, **kwargs: Any) -> ToolResult:
        """Read recent messages from a Slack channel."""
        channel = kwargs.get("channel", "")
        if not channel:
            return ToolResult(
                success=False, error="channel is required for read_channel"
            )

        limit = kwargs.get("limit", 20)
        client = self._get_client()
        response = await client.conversations_history(channel=channel, limit=limit)

        messages = response.data.get("messages", [])
        lines = []
        for msg in messages:
            user = msg.get("user", "unknown")
            text = msg.get("text", "")
            ts = msg.get("ts", "")
            lines.append(f"[{ts}] {user}: {text}")

        log.info("slack_tool.read", channel=channel, count=len(messages))
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No messages found.",
            data={"messages": messages},
        )

    async def _search_messages(self, **kwargs: Any) -> ToolResult:
        """Search messages across the workspace."""
        query = kwargs.get("query", "")
        if not query:
            return ToolResult(
                success=False, error="query is required for search_messages"
            )

        limit = kwargs.get("limit", 20)
        client = self._get_client()
        response = await client.search_messages(query=query, count=limit)

        matches = response.data.get("messages", {}).get("matches", [])
        lines = []
        for match in matches:
            channel_name = match.get("channel", {}).get("name", "unknown")
            user = match.get("username", "unknown")
            text = match.get("text", "")
            lines.append(f"#{channel_name} | {user}: {text}")

        log.info("slack_tool.search", query=query, count=len(matches))
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No messages matched.",
            data={"matches": matches},
        )

    async def _list_channels(self, **kwargs: Any) -> ToolResult:
        """List channels the bot is in."""
        limit = kwargs.get("limit", 20)
        client = self._get_client()
        response = await client.conversations_list(limit=limit)

        channels = response.data.get("channels", [])
        lines = []
        for ch in channels:
            name = ch.get("name", "unknown")
            ch_id = ch.get("id", "")
            topic = ch.get("topic", {}).get("value", "")
            lines.append(f"#{name} ({ch_id})" + (f" \u2014 {topic}" if topic else ""))

        log.info("slack_tool.list_channels", count=len(channels))
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No channels found.",
            data={"channels": channels},
        )

    async def _set_topic(self, **kwargs: Any) -> ToolResult:
        """Set a channel topic."""
        channel = kwargs.get("channel", "")
        topic = kwargs.get("topic", "")
        if not channel or not topic:
            return ToolResult(
                success=False, error="channel and topic are required for set_topic"
            )

        client = self._get_client()
        response = await client.conversations_setTopic(channel=channel, topic=topic)

        log.info("slack_tool.set_topic", channel=channel, topic=topic)
        return ToolResult(
            success=True,
            output=f"Topic set for {channel}: {topic}",
            data={"response": response.data},
        )

    async def _list_users(self, **kwargs: Any) -> ToolResult:
        """List workspace members."""
        limit = kwargs.get("limit", 20)
        client = self._get_client()
        response = await client.users_list(limit=limit)

        members = response.data.get("members", [])
        lines = []
        for user in members:
            name = user.get("real_name", user.get("name", "unknown"))
            uid = user.get("id", "")
            lines.append(f"{name} ({uid})")

        log.info("slack_tool.list_users", count=len(members))
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No users found.",
            data={"members": members},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_read_only(self, action: str) -> bool:
        """Return True if the action is read-only."""
        return action in self._READ_ACTIONS
