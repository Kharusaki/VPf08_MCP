"""Запуск MCP-сервера по HTTP-транспорту Streamable HTTP."""

from __future__ import annotations

import logging

from . import tools  # noqa: F401  # импорт регистрирует инструменты в FastMCP
from .app import db, mcp, settings

logger = logging.getLogger(__name__)


def main() -> None:
    """Инициализировать базу и запустить сервер до остановки процесса."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db.initialize()
    logger.info("MCP-сервер запускается: %s", settings.url)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
