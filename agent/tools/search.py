"""Инструмент «Веб-поиск» через DuckDuckGo (пакет ddgs)."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..config import SETTINGS

logger = logging.getLogger(__name__)

try:
    from ddgs import DDGS
except ImportError:  # запасной вариант для старого имени пакета
    from duckduckgo_search import DDGS  # type: ignore


def web_search(query: str, max_results: int = 5, region: str = "ru-ru") -> List[Dict[str, str]]:
    """Найти информацию в интернете и вернуть список результатов."""
    query = (query or "").strip()
    if not query:
        raise ValueError("Пустой поисковый запрос.")

    max_results = max(1, min(int(max_results), 10))
    attempts: List[Optional[str]] = [SETTINGS.http_proxy, None] if SETTINGS.http_proxy else [None]
    last_error: Optional[Exception] = None

    for proxy in attempts:
        try:
            ddgs = DDGS(proxy=proxy, timeout=20)
            try:
                raw = list(
                    ddgs.text(
                        query,
                        region=region,
                        safesearch="moderate",
                        max_results=max_results,
                    )
                )
            finally:
                close = getattr(ddgs, "close", None)
                if callable(close):
                    close()

            results: List[Dict[str, str]] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                results.append(
                    {
                        "title": str(item.get("title", "")).strip(),
                        "url": str(item.get("href") or item.get("url") or "").strip(),
                        "snippet": str(item.get("body") or item.get("snippet") or "").strip(),
                    }
                )
            if results:
                return results
            last_error = RuntimeError("DuckDuckGo не вернул результатов.")
        except Exception as exc:  # сеть/лимиты/изменения API
            last_error = exc
            logger.warning("Поиск не удался (proxy=%s): %s", bool(proxy), exc)

    raise RuntimeError(f"Поиск в интернете не удался: {last_error}")
