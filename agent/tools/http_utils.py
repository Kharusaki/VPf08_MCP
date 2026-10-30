"""Общие помощники для HTTP-запросов.

Главная идея: если в .env задан прокси, сначала пробуем через него,
а при сетевой ошибке — напрямую. Так агент работает и за VPN, и без него.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from ..config import SETTINGS

logger = logging.getLogger(__name__)

# Некоторые сайты блокируют запросы без User-Agent
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AI-Agent/1.0; +https://example.local)",
}


def _proxy_map() -> Optional[Dict[str, str]]:
    """Вернуть словарь прокси для requests или None."""
    proxy = SETTINGS.http_proxy
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def request(
    method: str,
    url: str,
    *,
    timeout: int = 15,
    headers: Optional[Dict[str, str]] = None,
    **kwargs: Any,
) -> requests.Response:
    """Выполнить HTTP-запрос с прокси и запасным прямым подключением."""
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)

    attempts = [True, False] if SETTINGS.http_proxy else [False]
    last_error: Optional[Exception] = None

    for use_proxy in attempts:
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                timeout=timeout,
                headers=merged_headers,
                proxies=_proxy_map() if use_proxy else None,
                **kwargs,
            )
            if use_proxy and response.status_code in {403, 407, 502, 503, 504}:
                logger.warning(
                    "Прокси вернул HTTP %s для %s, пробую напрямую",
                    response.status_code,
                    url,
                )
                continue
            return response
        except requests.RequestException as exc:
            last_error = exc
            logger.warning(
                "HTTP %s %s не удался (%s): %s",
                method.upper(),
                url,
                "через прокси" if use_proxy else "напрямую",
                exc,
            )

    raise requests.RequestException(f"Не удалось выполнить запрос к {url}: {last_error}")


def get_json(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 15,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    """GET-запрос и разбор JSON с понятной ошибкой при плохом статусе."""
    response = request("GET", url, params=params, timeout=timeout, headers=headers)
    if response.status_code >= 400:
        raise requests.HTTPError(
            f"Сервис вернул HTTP {response.status_code}: {response.text[:300]}",
            response=response,
        )
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(f"Сервис вернул не JSON: {response.text[:300]}") from exc
