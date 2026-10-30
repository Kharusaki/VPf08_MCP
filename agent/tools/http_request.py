"""Инструмент «HTTP-запрос» для обращения к внешним API.

Это не «делай что хочешь», а безопасный GET/POST с ограничениями:
- только http/https;
- запрещены localhost и приватные IP;
- ответ обрезается, чтобы не переполнить контекст модели.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from .http_utils import request

logger = logging.getLogger(__name__)

MAX_RESPONSE_CHARS = 100_000


def _validate_url(url: str) -> None:
    """Проверить схему и хост, чтобы не ходить во внутреннюю сеть."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Разрешены только http и https.")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("В URL не указан домен.")

    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
        raise ValueError("Запросы к локальным адресам запрещены.")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
        raise ValueError("Запросы к приватным IP-адресам запрещены.")

    # Если домен резолвится в приватный IP — тоже блокируем
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        addresses = set()
    for address in addresses:
        try:
            resolved = ipaddress.ip_address(address)
        except ValueError:
            continue
        if resolved.is_private or resolved.is_loopback or resolved.is_link_local:
            raise ValueError("Домен указывает на приватный IP-адрес — запрос запрещён.")


def http_request(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
) -> Dict[str, Any]:
    """Выполнить HTTP-запрос и вернуть статус, заголовки и текст ответа."""
    method = (method or "GET").upper()
    if method not in {"GET", "POST"}:
        raise ValueError("Разрешены только методы GET и POST.")
    if not url or not str(url).strip():
        raise ValueError("Не указан URL.")

    url = str(url).strip()
    _validate_url(url)
    timeout = max(1, min(int(timeout), 60))

    response = request(
        method,
        url,
        timeout=timeout,
        headers=headers,
        params=params,
        json=json_body,
        allow_redirects=True,
    )
    _validate_url(response.url)

    text = response.text or ""
    truncated = len(text) > MAX_RESPONSE_CHARS
    return {
        "status_code": response.status_code,
        "url": response.url,
        "content_type": response.headers.get("Content-Type", ""),
        "text": text[:MAX_RESPONSE_CHARS],
        "truncated": truncated,
    }
