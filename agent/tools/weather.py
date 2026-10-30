"""Инструмент «Погода» на бесплатном API Open-Meteo.

Сначала название города превращаем в координаты (geocoding),
затем запрашиваем текущую погоду по этим координатам.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from .http_utils import get_json

logger = logging.getLogger(__name__)

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

# Коды погоды WMO -> понятное описание на русском
WEATHER_CODES: Dict[int, str] = {
    0: "ясно",
    1: "преимущественно ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "слабая морось",
    53: "умеренная морось",
    55: "сильная морось",
    56: "слабая ледяная морось",
    57: "сильная ледяная морось",
    61: "слабый дождь",
    63: "умеренный дождь",
    65: "сильный дождь",
    66: "слабый ледяной дождь",
    67: "сильный ледяной дождь",
    71: "слабый снег",
    73: "умеренный снег",
    75: "сильный снег",
    77: "снежная крупа",
    80: "слабые ливни",
    81: "умеренные ливни",
    82: "сильные ливни",
    85: "слабый снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с небольшим градом",
    99: "гроза с сильным градом",
}


def _describe(code: Any) -> str:
    """Перевести числовой код погоды в текст."""
    try:
        return WEATHER_CODES.get(int(code), f"код погоды {code}")
    except (TypeError, ValueError):
        return "неизвестно"


def _find_city(city: str) -> Dict[str, Any]:
    """Найти город и вернуть первый результат geocoding."""
    data = get_json(
        GEO_URL,
        params={"name": city, "count": 1, "language": "ru", "format": "json"},
    )
    results = data.get("results") or []
    if not results:
        raise ValueError(f"Город «{city}» не найден. Попробуйте написать название иначе.")
    return results[0]


def get_weather(city: str) -> Dict[str, Any]:
    """Вернуть текущую погоду для города."""
    city = (city or "").strip()
    if not city:
        raise ValueError("Не указан город.")

    place = _find_city(city)
    latitude = place["latitude"]
    longitude = place["longitude"]

    data = get_json(
        WEATHER_URL,
        params={
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "wind_speed_10m,weather_code"
            ),
            "timezone": "auto",
        },
    )
    current = data.get("current") or {}

    return {
        "city": place.get("name", city),
        "country": place.get("country", ""),
        "region": place.get("admin1", ""),
        "latitude": latitude,
        "longitude": longitude,
        "temperature_c": current.get("temperature_2m"),
        "feels_like_c": current.get("apparent_temperature"),
        "humidity_percent": current.get("relative_humidity_2m"),
        "wind_kmh": current.get("wind_speed_10m"),
        "condition": _describe(current.get("weather_code")),
        "observed_at": current.get("time"),
        "source": "Open-Meteo",
    }
