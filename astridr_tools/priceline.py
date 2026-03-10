"""Priceline integration tool — travel search and booking.

Requires PRICELINE_API_KEY environment variable.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()

PRICELINE_BASE = "https://api.priceline.com/v3"


class PricelineTool(BaseTool):
    """Search and manage travel on Priceline: hotels, flights, deals."""

    name = "priceline"
    description = "Search Priceline for hotels, flights, and travel deals"
    approval_tier = "supervised"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "search_hotels",
                    "search_flights",
                    "get_hotel_details",
                    "get_flight_details",
                    "list_deals",
                ],
                "description": "The Priceline operation to perform.",
            },
            "destination": {
                "type": "string",
                "description": "Destination city or airport code.",
            },
            "origin": {
                "type": "string",
                "description": "Origin city or airport code (for flights).",
            },
            "check_in": {
                "type": "string",
                "description": "Check-in / departure date (YYYY-MM-DD).",
            },
            "check_out": {
                "type": "string",
                "description": "Check-out / return date (YYYY-MM-DD).",
            },
            "guests": {
                "type": "integer",
                "description": "Number of guests (default 1).",
                "default": 1,
            },
            "rooms": {
                "type": "integer",
                "description": "Number of rooms (default 1).",
                "default": 1,
            },
            "hotel_id": {
                "type": "string",
                "description": "Hotel ID for detail lookup.",
            },
            "flight_id": {
                "type": "string",
                "description": "Flight offer ID for detail lookup.",
            },
            "max_price": {
                "type": "number",
                "description": "Maximum price filter.",
            },
            "star_rating": {
                "type": "integer",
                "description": "Minimum star rating (1-5).",
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
        {"search_hotels", "search_flights", "get_hotel_details", "get_flight_details", "list_deals"}
    )

    def __init__(self) -> None:
        self._api_key = os.environ.get("PRICELINE_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Accept": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.AsyncClient(headers=headers, timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def is_read_only(self, action: str) -> bool:
        return action in self._READ_ACTIONS

    async def execute(self, **kwargs: Any) -> ToolResult:
        action: str = kwargs.get("action", "")
        if not action:
            return ToolResult(success=False, error="Missing required parameter: action")
        if not self._api_key:
            return ToolResult(success=False, error="PRICELINE_API_KEY not configured")

        dispatch = {
            "search_hotels": self._search_hotels,
            "search_flights": self._search_flights,
            "get_hotel_details": self._get_hotel_details,
            "get_flight_details": self._get_flight_details,
            "list_deals": self._list_deals,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult(success=False, error=f"Unknown action: {action}")

        try:
            return await handler(**kwargs)
        except httpx.HTTPStatusError as exc:
            log.error("priceline.http_error", status=exc.response.status_code, action=action)
            return ToolResult(
                success=False,
                error=f"Priceline API error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.HTTPError as exc:
            log.error("priceline.request_error", error=str(exc), action=action)
            return ToolResult(success=False, error=f"HTTP error: {exc}")

    async def _search_hotels(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        destination = kwargs.get("destination", "")
        check_in = kwargs.get("check_in", "")
        check_out = kwargs.get("check_out", "")
        if not destination or not check_in or not check_out:
            return ToolResult(
                success=False,
                error="destination, check_in, and check_out are required for search_hotels",
            )

        params: dict[str, Any] = {
            "destination": destination,
            "checkIn": check_in,
            "checkOut": check_out,
            "guests": kwargs.get("guests", 1),
            "rooms": kwargs.get("rooms", 1),
            "limit": kwargs.get("limit", 20),
        }
        if kwargs.get("max_price"):
            params["maxPrice"] = kwargs["max_price"]
        if kwargs.get("star_rating"):
            params["starRating"] = kwargs["star_rating"]

        resp = await client.get(f"{PRICELINE_BASE}/hotels/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        hotels = data.get("hotels", [])
        lines = [
            f"{h.get('id', '')} — {h.get('name', '')} "
            f"{'\u2605' * h.get('starRating', 0)} — ${h.get('price', {}).get('total', 'N/A')}/night"
            for h in hotels
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No hotels found",
            data={"hotels": hotels, "count": len(hotels)},
        )

    async def _search_flights(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        origin = kwargs.get("origin", "")
        destination = kwargs.get("destination", "")
        check_in = kwargs.get("check_in", "")
        if not origin or not destination or not check_in:
            return ToolResult(
                success=False,
                error="origin, destination, and check_in (departure date) are required for search_flights",
            )

        params: dict[str, Any] = {
            "origin": origin,
            "destination": destination,
            "departureDate": check_in,
            "passengers": kwargs.get("guests", 1),
            "limit": kwargs.get("limit", 20),
        }
        if kwargs.get("check_out"):
            params["returnDate"] = kwargs["check_out"]
        if kwargs.get("max_price"):
            params["maxPrice"] = kwargs["max_price"]

        resp = await client.get(f"{PRICELINE_BASE}/flights/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        flights = data.get("flights", [])
        lines = [
            f"{f.get('id', '')} — {f.get('airline', '')} "
            f"{f.get('departure', '')} \u2192 {f.get('arrival', '')} — ${f.get('price', {}).get('total', 'N/A')}"
            for f in flights
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No flights found",
            data={"flights": flights, "count": len(flights)},
        )

    async def _get_hotel_details(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        hotel_id = kwargs.get("hotel_id", "")
        if not hotel_id:
            return ToolResult(success=False, error="hotel_id is required")

        resp = await client.get(f"{PRICELINE_BASE}/hotels/{hotel_id}")
        resp.raise_for_status()
        data = resp.json()
        name = data.get("name", "")
        stars = data.get("starRating", 0)
        address = data.get("address", "")
        rating = data.get("guestRating", "N/A")
        amenities = ", ".join(data.get("amenities", [])[:5])
        output = (
            f"{name} {'\u2605' * stars}\n"
            f"Address: {address}\n"
            f"Guest Rating: {rating}\n"
            f"Amenities: {amenities}"
        )
        return ToolResult(success=True, output=output, data={"hotel": data})

    async def _get_flight_details(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        flight_id = kwargs.get("flight_id", "")
        if not flight_id:
            return ToolResult(success=False, error="flight_id is required")

        resp = await client.get(f"{PRICELINE_BASE}/flights/{flight_id}")
        resp.raise_for_status()
        data = resp.json()
        airline = data.get("airline", "")
        departure = data.get("departure", "")
        arrival = data.get("arrival", "")
        duration = data.get("duration", "")
        stops = data.get("stops", 0)
        price = data.get("price", {}).get("total", "N/A")
        output = (
            f"{airline} — ${price}\n"
            f"Departure: {departure} \u2192 Arrival: {arrival}\n"
            f"Duration: {duration} | Stops: {stops}"
        )
        return ToolResult(success=True, output=output, data={"flight": data})

    async def _list_deals(self, **kwargs: Any) -> ToolResult:
        client = self._ensure_client()
        params: dict[str, Any] = {"limit": kwargs.get("limit", 20)}
        if kwargs.get("destination"):
            params["destination"] = kwargs["destination"]

        resp = await client.get(f"{PRICELINE_BASE}/deals", params=params)
        resp.raise_for_status()
        data = resp.json()
        deals = data.get("deals", [])
        lines = [
            f"{d.get('id', '')} — {d.get('title', '')} "
            f"({d.get('type', '')}) — {d.get('discount', '')}"
            for d in deals
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "No deals found",
            data={"deals": deals},
        )
