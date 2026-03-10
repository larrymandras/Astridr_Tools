"""Weather tool — current conditions and forecasts via Open-Meteo (free, no API key)."""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from astridr.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class WeatherTool(BaseTool):
    """Get current weather conditions and forecasts for any location.

    Uses Open-Meteo APIs (free, no API key required):
    - Geocoding API for city name resolution
    - Weather Forecast API for current + forecast data
    """

    name = "weather"
    description = "Get current weather conditions and forecasts for any location"
    approval_tier = "read_only"

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City name or coordinates as 'lat,lon'.",
            },
            "forecast_days": {
                "type": "integer",
                "default": 3,
                "minimum": 1,
                "maximum": 7,
                "description": "Number of forecast days (1-7).",
            },
        },
        "required": ["location"],
        "additionalProperties": False,
    }

    GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
    WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

    # WMO Weather interpretation codes
    _WMO_CODES: dict[int, str] = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Foggy",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        56: "Light freezing drizzle",
        57: "Dense freezing drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        66: "Light freezing rain",
        67: "Heavy freezing rain",
        71: "Slight snow fall",
        73: "Moderate snow fall",
        75: "Heavy snow fall",
        77: "Snow grains",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        85: "Slight snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail",
    }

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create the httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Get weather for the specified location."""
        location = kwargs.get("location", "")
        if not location:
            return ToolResult(success=False, error="Missing required parameter: location")

        forecast_days = kwargs.get("forecast_days", 3)
        forecast_days = max(1, min(7, forecast_days))

        try:
            lat, lon, display_name = await self._geocode(location)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        except httpx.HTTPError as exc:
            log.error("weather.geocode_error", location=location, error=str(exc))
            return ToolResult(success=False, error=f"Geocoding error: {exc}")

        try:
            weather_data = await self._fetch_weather(lat, lon, forecast_days)
        except httpx.HTTPError as exc:
            log.error("weather.fetch_error", location=location, error=str(exc))
            return ToolResult(success=False, error=f"Weather API error: {exc}")

        formatted = self._format_weather(weather_data, display_name)
        log.info("weather.fetched", location=display_name, lat=lat, lon=lon)
        return ToolResult(
            success=True,
            output=formatted,
            data={
                "location": display_name,
                "latitude": lat,
                "longitude": lon,
                "weather": weather_data,
            },
        )

    async def _geocode(self, location: str) -> tuple[float, float, str]:
        """Resolve a location string to coordinates.

        Accepts either a city name or 'lat,lon' format.
        Returns (latitude, longitude, display_name).
        """
        # Check if it's already coordinates
        if "," in location:
            parts = location.split(",", 1)
            try:
                lat = float(parts[0].strip())
                lon = float(parts[1].strip())
                return lat, lon, f"{lat:.2f}, {lon:.2f}"
            except ValueError:
                pass  # Not coordinates, treat as city name

        client = self._ensure_client()
        resp = await client.get(
            self.GEOCODING_URL,
            params={"name": location, "count": 1, "language": "en", "format": "json"},
        )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            raise ValueError(f"Location not found: {location!r}")

        result = results[0]
        lat = result["latitude"]
        lon = result["longitude"]
        parts = [result.get("name", "")]
        if result.get("admin1"):
            parts.append(result["admin1"])
        if result.get("country"):
            parts.append(result["country"])
        display_name = ", ".join(p for p in parts if p)

        return lat, lon, display_name

    async def _fetch_weather(
        self, lat: float, lon: float, forecast_days: int
    ) -> dict[str, Any]:
        """Fetch weather data from Open-Meteo API."""
        client = self._ensure_client()
        resp = await client.get(
            self.WEATHER_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": (
                    "temperature_2m,relative_humidity_2m,apparent_temperature,"
                    "weather_code,wind_speed_10m,wind_direction_10m"
                ),
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_sum,wind_speed_10m_max"
                ),
                "forecast_days": forecast_days,
                "timezone": "auto",
            },
        )
        resp.raise_for_status()
        return resp.json()

    @classmethod
    def _format_weather(cls, data: dict[str, Any], location_name: str) -> str:
        """Format weather data as human-readable text."""
        lines: list[str] = [f"Weather for {location_name}"]
        lines.append("=" * len(lines[0]))

        # Current conditions
        current = data.get("current", {})
        if current:
            code = current.get("weather_code", 0)
            desc = cls._weather_code_to_description(code)
            lines.append("")
            lines.append("Current conditions:")
            lines.append(f"  {desc}")
            lines.append(f"  Temperature: {current.get('temperature_2m', 'N/A')}\u00b0C")
            lines.append(
                f"  Feels like: {current.get('apparent_temperature', 'N/A')}\u00b0C"
            )
            lines.append(
                f"  Humidity: {current.get('relative_humidity_2m', 'N/A')}%"
            )
            lines.append(
                f"  Wind: {current.get('wind_speed_10m', 'N/A')} km/h "
                f"({current.get('wind_direction_10m', 'N/A')}\u00b0)"
            )

        # Daily forecast
        daily = data.get("daily", {})
        dates = daily.get("time", [])
        if dates:
            lines.append("")
            lines.append("Forecast:")
            for i, date in enumerate(dates):
                code = daily.get("weather_code", [])[i] if i < len(daily.get("weather_code", [])) else 0
                desc = cls._weather_code_to_description(code)
                t_max = daily.get("temperature_2m_max", [])[i] if i < len(daily.get("temperature_2m_max", [])) else "N/A"
                t_min = daily.get("temperature_2m_min", [])[i] if i < len(daily.get("temperature_2m_min", [])) else "N/A"
                precip = daily.get("precipitation_sum", [])[i] if i < len(daily.get("precipitation_sum", [])) else 0
                wind = daily.get("wind_speed_10m_max", [])[i] if i < len(daily.get("wind_speed_10m_max", [])) else "N/A"

                lines.append(f"  {date}: {desc}")
                lines.append(f"    High: {t_max}\u00b0C  Low: {t_min}\u00b0C")
                lines.append(f"    Precipitation: {precip} mm  Wind: {wind} km/h")

        return "\n".join(lines)

    @classmethod
    def _weather_code_to_description(cls, code: int) -> str:
        """Convert a WMO weather code to a human-readable description."""
        return cls._WMO_CODES.get(code, f"Unknown (code {code})")

    def is_read_only(self, action: str = "") -> bool:
        """Weather is always read-only."""
        return True
