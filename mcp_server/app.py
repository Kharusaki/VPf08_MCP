"""Экземпляр FastMCP и общие зависимости инструментов."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .config import load_settings
from .database import CourseDatabase

settings = load_settings()
db = CourseDatabase(settings.db_path)

# stateless_http + json_response выбраны для локального учебного проекта:
# не нужно хранить сессии на сервере, а ответы идут обычным JSON вместо SSE.
mcp = FastMCP(
    "lingvapro-courses",
    instructions=(
        "Каталог языкового центра «ЛингваПро». Используйте инструменты для "
        "проверки курсов, цен, скидок, расписания и создания заявок. "
        "Не придумывайте данные, которых нет в ответах инструментов."
    ),
    host=settings.host,
    port=settings.port,
    streamable_http_path=settings.path,
    json_response=True,
    stateless_http=True,
)
