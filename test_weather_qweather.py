"""Offline tests for the QWeather MCP response mapping."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

import weather_mcp_server as weather


class FakeResponse:
    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


class FakeQWeatherClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, url: str, params: dict):
        if url.endswith("/geo/v2/city/lookup"):
            return FakeResponse(
                {
                    "code": "200",
                    "location": [
                        {
                            "name": "金水",
                            "id": "101180111",
                            "lat": "34.80",
                            "lon": "113.66",
                            "adm2": "郑州",
                            "adm1": "河南省",
                            "country": "中国",
                            "tz": "Asia/Shanghai",
                        }
                    ],
                }
            )
        if url.endswith("/v7/weather/now"):
            return FakeResponse(
                {
                    "code": "200",
                    "updateTime": "2026-08-14T10:05+08:00",
                    "fxLink": "https://www.qweather.com/",
                    "now": {
                        "obsTime": "2026-08-14T10:00+08:00",
                        "temp": "28",
                        "feelsLike": "31",
                        "text": "多云",
                        "wind360": "90",
                        "windDir": "东风",
                        "windScale": "2",
                        "windSpeed": "8",
                        "humidity": "72",
                        "precip": "0.0",
                        "pressure": "998",
                        "vis": "18",
                        "cloud": "65",
                    },
                }
            )
        if "/v7/weather/" in url:
            day = {
                "fxDate": "2026-08-14",
                "tempMax": "33",
                "tempMin": "25",
                "textDay": "多云",
                "textNight": "小雨",
                "windDirDay": "东风",
                "windScaleDay": "2-3",
                "windSpeedDay": "10",
                "humidity": "75",
                "precip": "1.2",
                "sunrise": "05:45",
                "sunset": "19:15",
            }
            return FakeResponse({"code": "200", "daily": [day, day, day]})
        raise AssertionError(f"Unexpected URL: {url}")


class QWeatherMappingTests(unittest.TestCase):
    def test_missing_configuration_is_structured_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = asyncio.run(weather.get_weather("郑州", 3))
        self.assertEqual(result["source"], "QWeather")
        self.assertIn("QWEATHER_API_KEY", result["error"])

    def test_qweather_response_is_normalized(self) -> None:
        environment = {
            "QWEATHER_API_KEY": "test-key",
            "QWEATHER_API_HOST": "test.qweather.example",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(
                weather.httpx,
                "AsyncClient",
                return_value=FakeQWeatherClient(),
            ),
        ):
            result = asyncio.run(weather.get_weather("郑州市金水区", 3))

        self.assertEqual(result["source"], "QWeather")
        self.assertEqual(result["location_id"], "101180111")
        self.assertEqual(result["current"]["temperature_c"], 28.0)
        self.assertEqual(result["current"]["observation_time"], "2026-08-14T10:00+08:00")
        self.assertEqual(len(result["forecast"]), 3)


if __name__ == "__main__":
    unittest.main()
