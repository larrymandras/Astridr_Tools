"""Garmin Connect integration tool — health and fitness tracking.

OAuth only. Requires GARMIN_CLIENT_ID + GARMIN_CLIENT_SECRET.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult
from astridr.tools.oauth import OAuthTokenManager

log = structlog.get_logger()

GARMIN_API_BASE = "https://apis.garmin.com/wellness-api/rest"


class GarminTool(BaseTool):
    """Track health and fitness via Garmin Connect: activities, sleep, heart rate."""

    name = "garmin"
    description = "Access Garmin health data — daily summaries, activities, sleep, heart rate"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "get_daily_summary",
                    "list_activities",
                    "get_activity",
                    "get_sleep_data",
                    "get_heart_rate",
                ],
                "description": "The Garmin operation to perform.",
            },
            "date": {
                "type": "string",
                "description": "Date in YYYY-MM-DD format (default today).",
            },
            "activity_id": {
                "type": "string",
                "description": "Activity ID for detail lookup.",
            },
            "start_date": {
                "type": "string",
                "description": "Start date for range queries (YYYY-MM-DD).",
            },
            "end_date": {
                "type": "string",
                "description": "End date for range queries (YYYY-MM-DD).",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 20).",
                "default": 20,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    _READ_ACTIONS: frozenset[str] = frozenset(
        {"get_daily_summary", "list_activities", "get_activity", "get_sleep_data", "get_heart_rate"}
    )

    def __init__(self) -> None:
        self._oauth = OAuthTokenManager(
            provider="garmin",
            client_id_env="GARMIN_CLIENT_ID",
            client_secret_env="GARMIN_CLIENT_SECRET",
            token_url="https://connectapi.garmin.com/oauth-service/oauth/token",
            scopes=["activity", "health", "sleep", "heartrate"],
        )
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        await self._oauth.close()

    def is_read_only(self, action: str) -> bool:
        return action in self._READ_ACTIONS

    async def _get_auth_headers(self) -> dict[str, str] | None:
        token = await self._oauth.get_access_token()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}"}

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")

        if not await self._oauth.is_authenticated():
            return ToolResult(
                success=False,
                error="Garmin OAuth not configured. Run: python -m astridr.tools.oauth_setup garmin",
            )

        dispatch = {
            "get_daily_summary": self._get_daily_summary,
            "list_activities": self._list_activities,
            "get_activity": self._get_activity,
            "get_sleep_data": self._get_sleep_data,
            "get_heart_rate": self._get_heart_rate,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("garmin.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Garmin API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("garmin.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _get_daily_summary(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        date = kwargs.get("date", "")
        if not date:
            return ToolResult(success=False, error="date is required for get_daily_summary")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(
            f"{GARMIN_API_BASE}/dailies", params={"date": date}, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        summaries = data.get("dailies", [])
        if not summaries:
            return ToolResult(success=True, output=f"No data for {date}", data={"date": date})

        s = summaries[0]
        steps = s.get("steps", 0)
        calories = s.get("totalKilocalories", 0)
        distance_m = s.get("distanceInMeters", 0)
        active_min = s.get("activeTimeInSeconds", 0) // 60
        output = (
            f"Daily Summary ({date}):\n"
            f"Steps: {steps:,} | Calories: {calories:,} kcal\n"
            f"Distance: {distance_m / 1000:.1f} km | Active: {active_min} min"
        )
        return ToolResult(success=True, output=output, data={"summary": s, "date": date})

    async def _list_activities(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        params: dict[str, Any] = {"limit": kwargs.get("limit", 20)}
        if kwargs.get("start_date"):
            params["startDate"] = kwargs["start_date"]
        if kwargs.get("end_date"):
            params["endDate"] = kwargs["end_date"]

        resp = await client.get(
            f"{GARMIN_API_BASE}/activities", params=params, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        activities = data.get("activities", [])
        lines = [
            f"{a.get('activityId', '')} — {a.get('activityType', '')} "
            f"({a.get('durationInSeconds', 0) // 60} min)"
            for a in activities
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No activities found",
            data={"activities": activities},
        )

    async def _get_activity(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        activity_id = kwargs.get("activity_id", "")
        if not activity_id:
            return ToolResult(success=False, error="activity_id is required")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(
            f"{GARMIN_API_BASE}/activities/{activity_id}", headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        activity_type = data.get("activityType", "")
        duration = data.get("durationInSeconds", 0) // 60
        calories = data.get("calories", 0)
        distance = data.get("distanceInMeters", 0)
        output = (
            f"Activity: {activity_id}\n"
            f"Type: {activity_type} | Duration: {duration} min\n"
            f"Calories: {calories} kcal | Distance: {distance / 1000:.1f} km"
        )
        return ToolResult(success=True, output=output, data={"activity": data})

    async def _get_sleep_data(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        date = kwargs.get("date", "")
        if not date:
            return ToolResult(success=False, error="date is required for get_sleep_data")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(
            f"{GARMIN_API_BASE}/sleeps", params={"date": date}, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        sleeps = data.get("sleeps", [])
        if not sleeps:
            return ToolResult(success=True, output=f"No sleep data for {date}", data={"date": date})

        s = sleeps[0]
        total_min = s.get("durationInSeconds", 0) // 60
        deep_min = s.get("deepSleepDurationInSeconds", 0) // 60
        light_min = s.get("lightSleepDurationInSeconds", 0) // 60
        rem_min = s.get("remSleepInSeconds", 0) // 60
        score = s.get("sleepScores", {}).get("overall", "N/A")
        output = (
            f"Sleep ({date}):\n"
            f"Total: {total_min // 60}h {total_min % 60}m | Score: {score}\n"
            f"Deep: {deep_min}m | Light: {light_min}m | REM: {rem_min}m"
        )
        return ToolResult(success=True, output=output, data={"sleep": s, "date": date})

    async def _get_heart_rate(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        date = kwargs.get("date", "")
        if not date:
            return ToolResult(success=False, error="date is required for get_heart_rate")

        headers = await self._get_auth_headers()
        if not headers:
            return ToolResult(success=False, error="Not authenticated")

        resp = await client.get(
            f"{GARMIN_API_BASE}/heartRates", params={"date": date}, headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        hr_data = data.get("heartRates", [])
        if not hr_data:
            return ToolResult(
                success=True, output=f"No heart rate data for {date}", data={"date": date}
            )

        hr = hr_data[0]
        resting = hr.get("restingHeartRateInBeatsPerMinute", "N/A")
        max_hr = hr.get("maxHeartRateInBeatsPerMinute", "N/A")
        min_hr = hr.get("minHeartRateInBeatsPerMinute", "N/A")
        output = (
            f"Heart Rate ({date}):\n"
            f"Resting: {resting} bpm | Min: {min_hr} bpm | Max: {max_hr} bpm"
        )
        return ToolResult(success=True, output=output, data={"heart_rate": hr, "date": date})
