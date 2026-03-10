"""ProfileTool — LLM-callable tool for listing and switching agent profiles."""

from __future__ import annotations

import json
from typing import Any, Callable, Awaitable

from astridr.agent.profiles import ProfileManager
from astridr.tools.base import BaseTool, ToolResult


# Callback type: async (profile_id, session) -> None
ProfileSwitchCallback = Callable[[str, Any], Awaitable[None]]


class ProfileTool(BaseTool):
    """Tool that lets the LLM list, switch, or query agent profiles.

    Actions:
        list    — returns all available profiles as JSON.
        switch  — switches the session's active profile.
        current — returns the active profile id and name.
    """

    name = "agent_profile"
    description = (
        "Manage runtime agent profiles. "
        "Actions: 'list' (show available profiles), "
        "'switch' (change active profile — requires profile_id), "
        "'current' (show the active profile)."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "switch", "current"],
                "description": "The action to perform.",
            },
            "profile_id": {
                "type": "string",
                "description": "Profile ID to switch to (required for 'switch' action).",
            },
        },
        "required": ["action"],
    }
    approval_tier = "read_only"

    def __init__(
        self,
        profile_manager: ProfileManager,
        switch_callback: ProfileSwitchCallback | None = None,
    ) -> None:
        self._manager = profile_manager
        self._switch_callback = switch_callback

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = kwargs.get("action", "")
        profile_id = kwargs.get("profile_id", "")

        if action == "list":
            return self._handle_list()
        elif action == "current":
            # The current profile is tracked on the session, not here.
            # We return a stub; the router populates the real value via
            # session metadata before the tool call reaches us.
            current_id = kwargs.get("_current_profile_id", "default")
            try:
                profile = self._manager.get(current_id)
                return ToolResult(
                    success=True,
                    output=f"Active profile: {profile.name} ({profile.id})",
                    data={"id": profile.id, "name": profile.name},
                )
            except KeyError:
                return ToolResult(
                    success=True,
                    output=f"Active profile: {current_id}",
                    data={"id": current_id},
                )
        elif action == "switch":
            return await self._handle_switch(profile_id, kwargs.get("_session"))
        else:
            return ToolResult(
                success=False,
                error=f"Unknown action: {action}. Use 'list', 'switch', or 'current'.",
            )

    def _handle_list(self) -> ToolResult:
        profiles = self._manager.list_profiles()
        data = [
            {
                "id": p.id,
                "name": p.name,
                "soul_override": p.soul_override,
                "temperature": p.temperature,
                "max_rounds": p.max_rounds,
            }
            for p in profiles
        ]
        return ToolResult(
            success=True,
            output=json.dumps(data, indent=2),
            data={"profiles": data},
        )

    async def _handle_switch(self, profile_id: str, session: Any) -> ToolResult:
        if not profile_id:
            return ToolResult(
                success=False,
                error="profile_id is required for 'switch' action.",
            )
        try:
            profile = self._manager.get(profile_id)
        except KeyError:
            available = [p.id for p in self._manager.list_profiles()]
            return ToolResult(
                success=False,
                error=f"Profile '{profile_id}' not found. Available: {available}",
            )

        if self._switch_callback is not None:
            await self._switch_callback(profile_id, session)

        return ToolResult(
            success=True,
            output=f"Switched to profile: {profile.name} ({profile.id})",
            data={"id": profile.id, "name": profile.name},
        )
