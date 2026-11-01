"""Инструмент «Веб-поиск» через DuckDuckGo с новостными запасными источниками."""

from __future__ import annotations

import html as html_lib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Dict, List, Optional

from ..config import SETTINGS
from .http_utils import request

logger = logging.getLogger(__name__)

try:
    from ddgs import DDGS
except ImportError:  # запасной вариант для старого имени пакета
    from duckduckgo_search import DDGS  # type: ignore

_MONTHS = (
    ("январ", 1),
    ("феврал", 2),
    ("март", 3),
    ("апрел", 4),
    ("май", 5),
    ("мая", 5),
    ("июн", 6),
    ("июл", 7),
    ("август", 8),
    ("сентябр", 9),
    ("октябр", 10),
    ("ноябр", 11),
    ("декабр", 12),
)
_DATE_NUMERIC = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DATE_WORDS = re.compile(
    r"\b(\d{1,2})\s+"
    r"(январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]|июн\w*|июл\w*|"
    r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\s+(\d{4})",
    re.I,
)
_DATE_EN_MONTH_FIRST = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b",
    re.I,
)
_DATE_EN_DAY_FIRST = re.compile(
    r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(\d{4})\b",
    re.I,
)
_EN_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_LENTA_LINK = re.compile(
    r'<a[^>]+href="((?:https?://lenta\.ru)?/news/\d{4}/\d{2}/\d{2}/[^"#?]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)


def _month_number(token: str) -> Optional[int]:
    token = token.lower()
    for stem, number in _MONTHS:
        if token.startswith(stem):
            return number
    return None


def _parse_date(query: str) -> Optional[date]:
    """Найти дату в запросе: 31.10.2026, 2026-10-31 или «31 октября 2026»."""
    match = _DATE_ISO.search(query)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None

    match = _DATE_NUMERIC.search(query)
    if match:
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        except ValueError:
            return None

    match = _DATE_EN_MONTH_FIRST.search(query)
    if match:
        month = _EN_MONTHS[match.group(1).lower()]
        try:
            return date(int(match.group(3)), month, int(match.group(2)))
        except ValueError:
            return None

    match = _DATE_EN_DAY_FIRST.search(query)
    if match:
        month = _EN_MONTHS[match.group(2).lower()]
        try:
            return date(int(match.group(3)), month, int(match.group(1)))
        except ValueError:
            return None

    match = _DATE_WORDS.search(query)
    if match:
        month = _month_number(match.group(2))
        if month:
            try:
                return date(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                return None
    return None


def _lenta_rubric(query: str) -> str:
    text = query.lower()
    if any(word in text for word in ("russia", "росси", "москв", "путин")):
        return "russia"
    if any(word in text for word in ("econom", "эконом", "бизнес", "рынок", "finance")):
        return "economics"
    if any(word in text for word in ("science", "наук", "техно", "technology")):
        return "science"
    if any(word in text for word in ("sport", "спорт")):
        return "sport"
    if any(word in text for word in ("culture", "культур")):
        return "culture"
    return "world"


def _lenta_archive(query: str, max_results: int) -> List[Dict[str, str]]:
    """Взять заголовки из архива Lenta.ru за указанную в запросе дату."""
    day = _parse_date(query)
    if not day:
        return []

    rubric = _lenta_rubric(query)
    url = f"https://lenta.ru/rubrics/{rubric}/{day:%Y/%m/%d}/"
    try:
        response = request("GET", url, timeout=20)
    except Exception as exc:
        logger.warning("Архив Lenta.ru недоступен: %s", exc)
        return []
    if response.status_code >= 400:
        logger.warning("Архив Lenta.ru вернул HTTP %s", response.status_code)
        return []

    results: List[Dict[str, str]] = []
    seen = set()
    for href, inner in _LENTA_LINK.findall(response.text or ""):
        title = html_lib.unescape(re.sub(r"<[^>]+>", " ", inner))
        title = re.sub(r"\s+", " ", title).strip()
        title = re.sub(
            r"\s+\d{1,2}:\d{2},\s+\d{1,2}\s+\S+\s+\d{4}\s+\S*$", "", title
        ).strip()
        if len(title) < 12 or href in seen:
            continue
        seen.add(href)
        link = href if href.startswith("http") else f"https://lenta.ru{href}"
        results.append(
            {
                "title": title,
                "url": link,
                "snippet": f"Lenta.ru, {day:%d.%m.%Y}",
            }
        )
        if len(results) >= max_results:
            break

    if results:
        logger.info("Lenta.ru: найдено %s заголовков за %s", len(results), day)
    return results


def _ddgs_search(query: str, max_results: int, region: str) -> List[Dict[str, str]]:
    attempts: List[Optional[str]] = (
        [SETTINGS.http_proxy, None] if SETTINGS.http_proxy else [None]
    )
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

    if last_error:
        logger.warning("DuckDuckGo без результатов: %s", last_error)
    return []


def _google_news_rss(query: str, max_results: int) -> List[Dict[str, str]]:
    """Запасной поиск через RSS Google News."""
    is_russian = bool(re.search(r"[А-Яа-яЁё]", query))
    params: Dict[str, str] = {"q": query}
    if is_russian:
        params.update({"hl": "ru", "gl": "RU", "ceid": "RU:ru"})
    else:
        params.update({"hl": "en-US", "gl": "US", "ceid": "US:en"})

    try:
        response = request(
            "GET",
            "https://news.google.com/rss/search",
            params=params,
            timeout=20,
        )
        if response.status_code >= 400:
            return []
        root = ET.fromstring(response.content)
    except Exception as exc:
        logger.warning("Google News RSS недоступен: %s", exc)
        return []

    results: List[Dict[str, str]] = []
    for item in root.findall(".//item")[:max_results]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        published = (item.findtext("pubDate") or "").strip()
        source_node = item.find("source")
        source = (source_node.text or "").strip() if source_node is not None else ""
        description = item.findtext("description") or ""
        snippet = html_lib.unescape(re.sub(r"<[^>]+>", " ", description))
        snippet = re.sub(r"\s+", " ", snippet).strip()
        meta = " | ".join(part for part in (published, source) if part)
        results.append(
            {
                "title": title,
                "url": link,
                "snippet": f"{meta}. {snippet}".strip(),
            }
        )
    return results


def _merge(results: List[Dict[str, str]], max_results: int) -> List[Dict[str, str]]:
    merged: List[Dict[str, str]] = []
    seen = set()
    for item in results:
        url = item.get("url", "")
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        merged.append(item)
        if len(merged) >= max_results:
            break
    return merged


def web_search(query: str, max_results: int = 5, region: str = "ru-ru") -> List[Dict[str, str]]:
    """Найти информацию в интернете и вернуть список результатов."""
    query = (query or "").strip()
    if not query:
        raise ValueError("Пустой поисковый запрос.")

    max_results = max(1, min(int(max_results), 10))

    archive_results = _lenta_archive(query, max_results)
    if len(archive_results) >= max_results:
        return archive_results

    results = _ddgs_search(query, max_results, region)
    combined = _merge(archive_results + results, max_results)
    if len(combined) >= max_results:
        return combined

    news_results = _google_news_rss(query, max_results)
    combined = _merge(combined + news_results, max_results)
    if combined:
        return combined

    raise RuntimeError(f"Поиск в интернете не дал результатов по запросу: {query}")
