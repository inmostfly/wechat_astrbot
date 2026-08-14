"""Local QWeather MCP server."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


if load_dotenv is not None:
    load_dotenv(application_directory() / ".env")


mcp = FastMCP(
    "catgirl-weather",
    instructions="使用和风天气查询城市实况与未来数日预报。",
)


class QWeatherError(RuntimeError):
    """Raised when QWeather configuration or response is invalid."""


def as_number(value: Any, number_type: type = float) -> int | float | None:
    if value is None or value == "":
        return None
    try:
        return number_type(value)
    except (TypeError, ValueError):
        return None


def qweather_config() -> tuple[str, str]:
    api_key = os.getenv("QWEATHER_API_KEY", "").strip()
    api_host = os.getenv("QWEATHER_API_HOST", "").strip().rstrip("/")
    missing = []
    if not api_key:
        missing.append("QWEATHER_API_KEY")
    if not api_host:
        missing.append("QWEATHER_API_HOST")
    if missing:
        raise QWeatherError(
            "和风天气尚未配置，请在 .env 中填写：" + "、".join(missing)
        )
    if "://" not in api_host:
        api_host = "https://" + api_host
    if not api_host.startswith("https://"):
        raise QWeatherError("QWEATHER_API_HOST 必须使用 HTTPS")
    return api_key, api_host


async def qweather_get(
    client: httpx.AsyncClient,
    api_host: str,
    path: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    try:
        response = await client.get(api_host + path, params=params)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException as error:
        raise QWeatherError(f"和风天气请求超时：{path}") from error
    except httpx.HTTPStatusError as error:
        raise QWeatherError(
            f"和风天气 HTTP 错误：{error.response.status_code}"
        ) from error
    except (httpx.HTTPError, ValueError) as error:
        raise QWeatherError(f"和风天气网络或响应错误：{error}") from error

    if data.get("code") != "200":
        raise QWeatherError(
            f"和风天气接口 {path} 返回状态码 {data.get('code', 'unknown')}"
        )
    return data


async def qweather_weather(location: str, forecast_days: int) -> dict[str, Any]:
    api_key, api_host = qweather_config()
    timeout = httpx.Timeout(15.0, connect=8.0)
    headers = {
        "X-QW-Api-Key": api_key,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        geo = await qweather_get(
            client,
            api_host,
            "/geo/v2/city/lookup",
            {"location": location, "number": 1, "lang": "zh"},
        )
        places = geo.get("location") or []
        if not places:
            raise QWeatherError(f"和风天气找不到地点：{location}")
        place = places[0]
        location_id = place["id"]
        forecast_endpoint = "3d" if forecast_days <= 3 else "7d"

        current_data, daily_data = await asyncio.gather(
            qweather_get(
                client,
                api_host,
                "/v7/weather/now",
                {"location": location_id, "lang": "zh", "unit": "m"},
            ),
            qweather_get(
                client,
                api_host,
                f"/v7/weather/{forecast_endpoint}",
                {"location": location_id, "lang": "zh", "unit": "m"},
            ),
        )

    now = current_data["now"]
    forecasts = []
    for day in (daily_data.get("daily") or [])[:forecast_days]:
        day_text = day.get("textDay") or "未知"
        night_text = day.get("textNight") or "未知"
        condition = day_text if day_text == night_text else f"{day_text}转{night_text}"
        forecasts.append(
            {
                "date": day.get("fxDate"),
                "condition": condition,
                "day_condition": day_text,
                "night_condition": night_text,
                "temperature_max_c": as_number(day.get("tempMax")),
                "temperature_min_c": as_number(day.get("tempMin")),
                "precipitation_sum_mm": as_number(day.get("precip")),
                "relative_humidity_percent": as_number(day.get("humidity"), int),
                "wind_direction_day": day.get("windDirDay"),
                "wind_scale_day": day.get("windScaleDay"),
                "wind_speed_day_kmh": as_number(day.get("windSpeedDay")),
                "sunrise": day.get("sunrise"),
                "sunset": day.get("sunset"),
            }
        )

    display_parts = list(
        dict.fromkeys(
            part
            for part in (
                place.get("name"),
                place.get("adm2"),
                place.get("adm1"),
                place.get("country"),
            )
            if part
        )
    )
    return {
        "query": location,
        "resolved_location": "，".join(display_parts),
        "location_id": location_id,
        "latitude": as_number(place.get("lat")),
        "longitude": as_number(place.get("lon")),
        "timezone": place.get("tz"),
        "current": {
            "observation_time": now.get("obsTime"),
            "condition": now.get("text"),
            "temperature_c": as_number(now.get("temp")),
            "apparent_temperature_c": as_number(now.get("feelsLike")),
            "relative_humidity_percent": as_number(now.get("humidity"), int),
            "precipitation_mm": as_number(now.get("precip")),
            "cloud_cover_percent": as_number(now.get("cloud"), int),
            "pressure_hpa": as_number(now.get("pressure")),
            "visibility_km": as_number(now.get("vis")),
            "wind_speed_kmh": as_number(now.get("windSpeed")),
            "wind_direction_degrees": as_number(now.get("wind360"), int),
            "wind_direction": now.get("windDir"),
            "wind_scale": now.get("windScale"),
        },
        "forecast": forecasts,
        "provider_update_time": current_data.get("updateTime"),
        "source_url": current_data.get("fxLink"),
        "source": "QWeather",
    }


@mcp.tool()
async def get_weather(location: str, forecast_days: int = 3) -> dict[str, Any]:
    """通过和风天气查询指定城市当前实况和未来预报。

    Args:
        location: 城市、区县或地点名称，例如“郑州”“北京市海淀区”。
        forecast_days: 返回1到7天预报，默认3天。
    """

    location = location.strip()
    if len(location) < 2:
        return {"source": "QWeather", "error": "地点名称至少需要两个字符"}
    forecast_days = max(1, min(int(forecast_days), 7))
    try:
        return await qweather_weather(location, forecast_days)
    except QWeatherError as error:
        return {"source": "QWeather", "error": str(error)}


def run_server() -> None:
    """Run the weather server over stdio."""

    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
