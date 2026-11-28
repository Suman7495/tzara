"""Weather for a place, from Open-Meteo: free, no key, built for programs."""

from __future__ import annotations

import re
from typing import Any, ClassVar

import httpx

from tzara.config import WebSettings
from tzara.tools.web import Sanitizer, WebError


class WeatherReport:
    """Answers "what is the weather in Darmstadt" with real numbers, not a search.

    Search engines return weather sites that refuse programs (a 403 for accuweather.com), so a
    question about the weather got links and no answer. Open-Meteo is an open weather service made
    for programs: one request finds the place, one gives the forecast. No model request is made
    here; the place is picked out of the question by its words.
    """

    GEOCODE: ClassVar[str] = "https://geocoding-api.open-meteo.com/v1/search"
    FORECAST: ClassVar[str] = "https://api.open-meteo.com/v1/forecast"
    SOURCE: ClassVar[str] = "[open-meteo.com](https://open-meteo.com/)"
    CODES: ClassVar[dict[int, str]] = {
        0: "clear sky",
        1: "mostly clear",
        2: "partly cloudy",
        3: "overcast",
        45: "fog",
        48: "freezing fog",
        51: "light drizzle",
        53: "drizzle",
        55: "heavy drizzle",
        56: "freezing drizzle",
        57: "freezing drizzle",
        61: "light rain",
        63: "rain",
        65: "heavy rain",
        66: "freezing rain",
        67: "freezing rain",
        71: "light snow",
        73: "snow",
        75: "heavy snow",
        77: "snow grains",
        80: "light showers",
        81: "showers",
        82: "heavy showers",
        85: "snow showers",
        86: "heavy snow showers",
        95: "thunderstorm",
        96: "thunderstorm with hail",
        99: "thunderstorm with hail",
    }
    DAYS: ClassVar[tuple[str, ...]] = ("Today", "Tomorrow", "The day after")

    def __init__(self, settings: WebSettings, client: httpx.Client | None = None) -> None:
        """Create the source.

        Args:
            settings: Web settings: timeout, user agent, units and how many days.
            client: HTTP client. Replaced in tests.
        """
        self._settings = settings
        self._triggers = re.compile(settings.weather_words)
        self._after_in = re.compile(settings.weather_place)
        self._noise = frozenset(settings.weather_noise)
        self._client = client or httpx.Client(
            timeout=settings.search_timeout_seconds, headers={"User-Agent": settings.user_agent}
        )

    def place(self, question: str) -> str | None:
        """The place a weather question is about, or ``None`` if it names none.

        Found after "in", "for", "at" or "near" ("weather like in Darmstadt today"), or as the
        words before the weather word ("Darmstadt weather").
        """
        text = question.lower().replace("\u2019", "'").strip()
        trigger = self._triggers.search(text)
        if trigger is None:
            return None
        candidates: list[str] = []
        for found in self._after_in.finditer(text):
            candidates.append(found.group(1))
        candidates.append(text[: trigger.start()])
        for candidate in candidates:
            words = [word.strip("'\"") for word in re.findall(r"[^\W\d_][\w'.-]*", candidate)]
            kept = [
                word
                for word in words
                if word and word.replace("'", "") not in self._noise and word not in self._noise
            ]
            if kept:
                return " ".join(kept[-3:]).title()[:60]
        return None

    def applies(self, question: str) -> bool:
        """Whether the question is about the weather somewhere that it names."""
        return self.place(question) is not None

    def asks_about_weather(self, question: str) -> bool:
        """Whether the question is about the weather at all, whether or not it names a place."""
        return self._triggers.search(question.lower()) is not None

    def report(self, question: str, home: str | None = None) -> str:
        """The weather for the place in ``question`` (or ``home``), as text to answer from.

        Raises:
            WebError: If there is no place, it is not found, or the service did not answer.
        """
        place = self.place(question) or home
        if place is None:
            raise WebError("The question names no place.")
        try:
            name, latitude, longitude = self._find(place)
            data = self._forecast(latitude, longitude)
        except (httpx.HTTPError, ValueError, KeyError) as error:
            raise WebError(
                f"The weather service did not answer: {error.__class__.__name__}"
            ) from error
        return self._compose(name, data)

    def _find(self, place: str) -> tuple[str, float, float]:
        response = self._client.get(
            self.GEOCODE, params={"name": place, "count": 1, "language": "en", "format": "json"}
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results:
            raise WebError(f"No place called {place!r} was found.")
        best = results[0]
        parts = [best.get("name"), best.get("admin1"), best.get("country")]
        name = ", ".join(dict.fromkeys(Sanitizer.clean(str(part)) for part in parts if part))
        return name, float(best["latitude"]), float(best["longitude"])

    def _forecast(self, latitude: float, longitude: float) -> dict[str, Any]:
        fahrenheit = self._settings.temperature_unit == "fahrenheit"
        response = self._client.get(
            self.FORECAST,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "precipitation,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": len(self.DAYS),
                "temperature_unit": "fahrenheit" if fahrenheit else "celsius",
            },
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data

    def _compose(self, name: str, data: dict[str, Any]) -> str:
        unit = "°F" if self._settings.temperature_unit == "fahrenheit" else "°C"
        now = data["current"]
        daily = data["daily"]
        sky = self.CODES.get(int(now["weather_code"]), "unsettled")
        rain = "rain falling" if float(now.get("precipitation") or 0) > 0 else "no rain falling"
        lines = [
            f"Weather for {name} (Open-Meteo, local time {str(now['time']).replace('T', ' ')}):",
            f"Now: {round(now['temperature_2m'])}{unit} "
            f"(feels like {round(now['apparent_temperature'])}{unit}), {sky}, "
            f"wind {round(now['wind_speed_10m'])} km/h, "
            f"humidity {round(now['relative_humidity_2m'])}%, {rain}.",
        ]
        for index, label in enumerate(self.DAYS[: len(daily["time"])]):
            low = round(daily["temperature_2m_min"][index])
            high = round(daily["temperature_2m_max"][index])
            chance = daily["precipitation_probability_max"][index]
            sky = self.CODES.get(int(daily["weather_code"][index]), "unsettled")
            lines.append(
                f"{label} ({daily['time'][index]}): {low} to {high}{unit}, {sky}"
                + (f", chance of rain {chance}%." if chance is not None else ".")
            )
        lines.append(f"Source: {self.SOURCE}")
        return "\n".join(lines)
