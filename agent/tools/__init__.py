"""Пакет инструментов агента.

Каждый инструмент — это функция, которую LLM может вызвать через function calling.
Инструменты разделены по файлам для удобства чтения.
"""

from .calculator import calculate
from .crypto import get_crypto_price, get_crypto_quote
from .currency import get_currency_quote, get_currency_rate
from .file_io import list_directory, read_file, write_file
from .http_request import http_request as http_api_request
from .page_reader import read_url
from .qr import generate_qr
from .reminders import (
    add_reminder,
    clear_reminders,
    get_due_reminders,
    list_reminders,
    mark_reminder_sent,
)
from .search import web_search
from .terminal import run_terminal_command
from .weather import get_weather

__all__ = [
    "get_weather",
    "get_crypto_price",
    "get_crypto_quote",
    "get_currency_rate",
    "get_currency_quote",
    "web_search",
    "http_api_request",
    "read_url",
    "read_file",
    "write_file",
    "list_directory",
    "run_terminal_command",
    "generate_qr",
    "calculate",
    "add_reminder",
    "list_reminders",
    "clear_reminders",
    "get_due_reminders",
    "mark_reminder_sent",
]
