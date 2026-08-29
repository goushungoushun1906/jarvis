"""Weather plugin — sample JARVIS plugin with mock weather data."""

from __future__ import annotations

import logging
import random
from typing import Any

from app.plugins.base import BasePlugin, ToolResult

logger = logging.getLogger("jarvis")

# Mock weather database — used when no real API key is configured
_MOCK_WEATHER: dict[str, dict] = {
    "beijing": {
        "city": "Beijing",
        "temperature": 22,
        "conditions": "Partly cloudy",
        "humidity": 45,
        "wind_speed": 12,
    },
    "tokyo": {
        "city": "Tokyo",
        "temperature": 26,
        "conditions": "Sunny",
        "humidity": 60,
        "wind_speed": 8,
    },
    "london": {
        "city": "London",
        "temperature": 15,
        "conditions": "Overcast with light rain",
        "humidity": 78,
        "wind_speed": 20,
    },
    "new york": {
        "city": "New York",
        "temperature": 18,
        "conditions": "Clear skies",
        "humidity": 40,
        "wind_speed": 15,
    },
    "sydney": {
        "city": "Sydney",
        "temperature": 28,
        "conditions": "Sunny with scattered clouds",
        "humidity": 55,
        "wind_speed": 10,
    },
    "paris": {
        "city": "Paris",
        "temperature": 17,
        "conditions": "Light drizzle",
        "humidity": 72,
        "wind_speed": 18,
    },
    "shanghai": {
        "city": "Shanghai",
        "temperature": 24,
        "conditions": "Humid with haze",
        "humidity": 80,
        "wind_speed": 6,
    },
    "moscow": {
        "city": "Moscow",
        "temperature": 5,
        "conditions": "Snow flurries",
        "humidity": 65,
        "wind_speed": 25,
    },
}


class WeatherPlugin(BasePlugin):
    """A sample plugin that provides weather information.

    Uses mock data by default.  When a real API key is configured,
    it could be extended to call a live weather service.
    """

    async def on_load(self) -> None:
        logger.info("WeatherPlugin v%s loaded", self.version)

    async def on_enable(self) -> None:
        logger.info("WeatherPlugin enabled — mock data ready")

    async def on_disable(self) -> None:
        logger.info("WeatherPlugin disabled")

    async def on_unload(self) -> None:
        logger.info("WeatherPlugin unloaded")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def execute(self, command_name: str, args: dict[str, Any]) -> ToolResult:
        """Dispatch command by name."""
        if command_name == "get_weather":
            return await self._handle_get_weather(args)
        return ToolResult(
            f"Unknown command: {command_name}", success=False
        )

    async def _handle_get_weather(self, args: dict[str, Any]) -> ToolResult:
        city = args.get("city", "").strip()
        units = args.get("units")

        if not city:
            # Fall back to default city from config
            config = await self.get_config()
            city = config.get("default_city", "Beijing")

        if not units:
            config = await self.get_config()
            units = config.get("default_units", "celsius")

        key = city.lower()
        data = _MOCK_WEATHER.get(key)

        if data is None:
            # Generate random mock data for unknown cities
            data = {
                "city": city.title(),
                "temperature": random.randint(-5, 35),
                "conditions": random.choice(
                    ["Sunny", "Cloudy", "Rainy", "Partly cloudy", "Windy", "Foggy"]
                ),
                "humidity": random.randint(20, 90),
                "wind_speed": random.randint(0, 30),
            }

        temp = data["temperature"]
        if units == "fahrenheit":
            temp = round(temp * 9 / 5 + 32, 1)
            unit_symbol = "F"
        else:
            unit_symbol = "C"

        # Check if configured for live API
        config = await self.get_config()
        api_key = config.get("api_key", "")
        source = "live API" if api_key else "mock data"

        response_lines = [
            f"Weather for {data['city']}:",
            f"  Temperature: {temp} deg {unit_symbol}",
            f"  Conditions: {data['conditions']}",
            f"  Humidity: {data['humidity']}%",
            f"  Wind speed: {data['wind_speed']} km/h",
            f"  (Data source: {source})",
        ]

        return ToolResult(
            content="\n".join(response_lines),
            success=True,
            metadata={
                "city": data["city"],
                "temperature": temp,
                "units": units,
                "conditions": data["conditions"],
                "humidity": data["humidity"],
                "wind_speed": data["wind_speed"],
                "source": source,
            },
        )
