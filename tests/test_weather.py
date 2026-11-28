"""Weather answers come from Open-Meteo, not from a search that returns blocked pages."""

from __future__ import annotations

import httpx
import pytest

from tzara.config import WebSettings
from tzara.tools.weather import WeatherReport
from tzara.tools.web import WebError

FORECAST = {
    "current": {
        "time": "2026-11-28T01:45",
        "temperature_2m": 4.2,
        "apparent_temperature": 1.0,
        "relative_humidity_2m": 80,
        "precipitation": 0.0,
        "weather_code": 3,
        "wind_speed_10m": 12.0,
    },
    "daily": {
        "time": ["2026-11-28", "2026-11-29", "2026-11-30"],
        "weather_code": [3, 61, 0],
        "temperature_2m_max": [6.0, 7.0, 9.0],
        "temperature_2m_min": [2.0, 3.0, 1.0],
        "precipitation_probability_max": [40, 80, 0],
    },
}


def report_with(handler) -> WeatherReport:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return WeatherReport(WebSettings(), client)


def service(request: httpx.Request) -> httpx.Response:
    if "geocoding" in request.url.host:
        if request.url.params["name"] == "Nowhereville":
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "Darmstadt",
                        "admin1": "Hesse",
                        "country": "Germany",
                        "latitude": 49.87,
                        "longitude": 8.65,
                    }
                ]
            },
        )
    return httpx.Response(200, json=FORECAST)


class TestThePlace:
    @pytest.mark.parametrize(
        ("question", "place"),
        [
            ("hey, whats the weather like in darmstadt", "Darmstadt"),
            ("darmstadt weather", "Darmstadt"),
            ("will it rain in New York tomorrow?", "New York"),
            ("temperature in Munich", "Munich"),
        ],
    )
    def test_is_found_in_the_words(self, question, place):
        assert report_with(service).place(question) == place

    @pytest.mark.parametrize("question", ["whats the weather", "how is the weather today", "hi"])
    def test_a_question_without_a_place_is_left_to_the_search(self, question):
        assert not report_with(service).applies(question)


class TestTheReport:
    def test_says_how_it_is_now_and_the_next_days(self):
        text = report_with(service).report("weather in darmstadt")
        assert "Darmstadt, Hesse, Germany" in text
        assert "Now: 4°C (feels like 1°C), overcast, wind 12 km/h, humidity 80%" in text
        assert "Tomorrow (2026-11-29): 3 to 7°C, light rain, chance of rain 80%." in text
        assert "[open-meteo.com](https://open-meteo.com/)" in text

    def test_an_unknown_place_is_an_error_for_the_caller_to_search_instead(self):
        with pytest.raises(WebError):
            report_with(service).report("weather in Nowhereville")

    def test_a_service_that_fails_is_an_error(self):
        broken = report_with(lambda request: httpx.Response(500))
        with pytest.raises(WebError):
            broken.report("weather in darmstadt")
