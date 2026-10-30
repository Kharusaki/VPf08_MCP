"""Реестр инструментов: описания для LLM и безопасный вызов.

LLM не вызывает Python-функции напрямую. Она возвращает имя инструмента
и JSON-аргументы, а этот модуль проверяет имя, вызывает функцию и
упаковывает результат/ошибку в понятный для модели словарь.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..config import SETTINGS
from . import (
    calculator,
    crypto,
    currency,
    file_io,
    http_request,
    qr,
    reminders,
    search,
    terminal,
    weather,
)

logger = logging.getLogger(__name__)

Handler = Callable[[Dict[str, Any], Dict[str, Any]], Any]


def _chat_id(context: Dict[str, Any]) -> str:
    """ID чата для напоминаний (в CLI используем 'cli')."""
    return str(context.get("chat_id") or "cli")


# --- Обработчики: принимают аргументы и контекст, возвращают результат ---

def _weather(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    return weather.get_weather(args["city"])


def _crypto(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    coin = args["coin"]
    currency_code = args.get("currency", "usd")
    price = crypto.get_crypto_price(coin, currency_code)
    return {
        "coin": coin,
        "currency": currency_code.upper(),
        "price": price,
        "formatted": f"{price:,.2f} {currency_code.upper()}",
    }


def _currency(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    base = args["base"]
    target = args["target"]
    amount = float(args.get("amount", 1))
    rate = currency.get_currency_rate(base, target)
    return {
        "base": base.upper(),
        "target": target.upper(),
        "rate": rate,
        "amount": amount,
        "converted": amount * rate,
    }


def _search(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    return search.web_search(
        args["query"],
        max_results=int(args.get("max_results", 5)),
        region=args.get("region", "ru-ru"),
    )


def _http(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    return http_request.http_request(
        url=args["url"],
        method=args.get("method", "GET"),
        headers=args.get("headers"),
        params=args.get("params"),
        json_body=args.get("json_body"),
        timeout=int(args.get("timeout", 20)),
    )


def _read_file(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    return file_io.read_file(args["path"], max_bytes=args.get("max_bytes"))


def _write_file(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    return file_io.write_file(args["path"], args["content"], bool(args.get("append", False)))


def _list_dir(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    return file_io.list_directory(args.get("path", "."))


def _terminal(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    return terminal.run_terminal_command(args["command"])


def _qr(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    relative = qr.generate_qr(args["text"], args.get("filename"))
    absolute = (SETTINGS.workspace_dir / relative).resolve()
    return {
        "path": relative,
        "artifact": str(absolute),
        "message": f"QR-код сохранён в workspace/{relative}",
    }


def _calc(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    return calculator.calculate(args["expression"])


def _add_reminder(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    return reminders.add_reminder(args["text"], args["when"], chat_id=_chat_id(context))


def _list_reminders(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    return reminders.list_reminders(
        chat_id=_chat_id(context),
        include_sent=bool(args.get("include_sent", False)),
    )


def _clear_reminders(args: Dict[str, Any], context: Dict[str, Any]) -> Any:
    count = reminders.clear_reminders(chat_id=_chat_id(context))
    return {"deleted": count}


HANDLERS: Dict[str, Handler] = {
    "get_weather": _weather,
    "get_crypto_price": _crypto,
    "get_currency_rate": _currency,
    "web_search": _search,
    "http_request": _http,
    "read_file": _read_file,
    "write_file": _write_file,
    "list_directory": _list_dir,
    "run_terminal_command": _terminal,
    "generate_qr": _qr,
    "calculate": _calc,
    "add_reminder": _add_reminder,
    "list_reminders": _list_reminders,
    "clear_reminders": _clear_reminders,
}


# --- Описания инструментов в формате OpenAI Function Calling ---

TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Найти актуальную информацию в интернете через DuckDuckGo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос."},
                    "max_results": {"type": "integer", "description": "Сколько результатов вернуть (1-10).", "default": 5},
                    "region": {"type": "string", "description": "Регион поиска, например ru-ru.", "default": "ru-ru"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Узнать текущую погоду в любом городе мира.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "Название города, например Москва или Tokyo."},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_crypto_price",
            "description": "Узнать текущий курс криптовалюты (Bitcoin, Ethereum и др.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin": {"type": "string", "description": "Монета: bitcoin, btc, ethereum, eth, ton и т.п."},
                    "currency": {"type": "string", "description": "Валюта: usd, eur, rub.", "default": "usd"},
                },
                "required": ["coin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_currency_rate",
            "description": "Узнать курс обычных валют и при необходимости пересчитать сумму.",
            "parameters": {
                "type": "object",
                "properties": {
                    "base": {"type": "string", "description": "Из какой валюты, например USD."},
                    "target": {"type": "string", "description": "В какую валюту, например RUB."},
                    "amount": {"type": "number", "description": "Сумма для пересчёта.", "default": 1},
                },
                "required": ["base", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": "Сделать HTTP GET/POST запрос к внешнему API и вернуть ответ.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Полный http/https URL."},
                    "method": {"type": "string", "enum": ["GET", "POST"], "default": "GET"},
                    "headers": {"type": "object", "description": "HTTP-заголовки."},
                    "params": {"type": "object", "description": "Query-параметры."},
                    "json_body": {"type": "object", "description": "JSON-тело для POST."},
                    "timeout": {"type": "integer", "description": "Таймаут в секундах.", "default": 20},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Прочитать текстовый файл из папки workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь внутри workspace, например notes.txt."},
                    "max_bytes": {"type": "integer", "description": "Максимальный размер файла в байтах."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Записать текст в файл внутри workspace (можно дописать в конец).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь внутри workspace, например notes.txt."},
                    "content": {"type": "string", "description": "Текст для записи."},
                    "append": {"type": "boolean", "description": "True — дописать в конец, False — перезаписать.", "default": False},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Показать файлы и папки внутри workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Папка внутри workspace.", "default": "."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "description": (
                "Выполнить безопасную read-only команду: ls/dir, pwd, cat/type, echo, "
                "where/which, python --version, pip --version, git status, date, whoami."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Команда из белого списка."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_qr",
            "description": "Сгенерировать QR-код с текстом или ссылкой и сохранить PNG в workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Текст или ссылка для QR-кода."},
                    "filename": {"type": "string", "description": "Необязательное имя PNG-файла."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Вычислить математическое выражение: арифметика, sqrt, sin, log, pi и т.п.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Выражение, например (2+3)*sqrt(16)."},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_reminder",
            "description": "Добавить напоминание. Бот пришлёт сообщение в этот чат в указанное время.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "О чём напомнить."},
                    "when": {"type": "string", "description": "Когда: ISO 8601, '2026-10-31 18:00', '+30m', '+2h'."},
                },
                "required": ["text", "when"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "Показать активные напоминания текущего чата.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_sent": {"type": "boolean", "description": "Показать и уже отправленные.", "default": False},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_reminders",
            "description": "Удалить все напоминания текущего чата.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def execute_tool(
    name: str,
    arguments: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Вызвать инструмент по имени и вернуть унифицированный результат."""
    handler = HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"Неизвестный инструмент: {name}"}
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "Аргументы инструмента должны быть JSON-объектом."}

    try:
        result = handler(arguments, context or {})
    except Exception as exc:
        logger.exception("Ошибка инструмента %s", name)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    artifacts: List[str] = []
    if isinstance(result, dict):
        artifact = result.get("artifact")
        if isinstance(artifact, str) and Path(artifact).is_file():
            artifacts.append(artifact)

    return {"ok": True, "result": result, "artifacts": artifacts}
