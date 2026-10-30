"""Логика AI-агента: цикл «вопрос -> инструменты -> ответ».

Используем OpenAI Function Calling:
1. Отправляем модели вопрос и список инструментов.
2. Если модель просит инструмент — вызываем его.
3. Возвращаем результат модели и повторяем, пока она не даст финальный ответ.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .config import SETTINGS
from .memory import add_message, get_history
from .tools.registry import TOOL_SPECS, execute_tool

logger = logging.getLogger(__name__)

AGENT_INSTRUCTIONS = """
Ты локальный AI-агент. Твоя задача — помогать пользователю и выполнять задачи,
используя инструменты, а не выдумывая данные.

Правила:
- Отвечай на языке пользователя, структурированно и по делу.
- Для свежих данных (погода, курсы, новости, поиск) обязательно вызывай инструмент.
- Можно вызывать несколько инструментов подряд, если задача составная.
- Если для точного ответа не хватает деталей — задай один короткий уточняющий вопрос.
- Для напоминаний используй add_reminder; время передавай в ISO 8601 или формате +30m/+2h.
- Не показывай пользователю внутренний JSON и технические детали инструментов.
- Если инструмент вернул ошибку, объясни её простыми словами и предложи альтернативу.
- Ссылки из веб-поиска указывай в ответе.
""".strip()


class AgentError(RuntimeError):
    """Понятная ошибка агента для CLI и бота."""


@dataclass
class AgentReply:
    """Результат одного запуска агента."""

    text: str
    artifacts: List[str] = field(default_factory=list)
    model: str = ""


class Agent:
    """AI-агент с доступом к инструментам и памяти."""

    def __init__(self) -> None:
        if not SETTINGS.api_key:
            raise AgentError("В .env не задан OPENAI_API_KEY.")
        self.client = OpenAI(
            api_key=SETTINGS.api_key,
            base_url=SETTINGS.base_url,
            timeout=SETTINGS.request_timeout,
        )

    def _system_prompt(self) -> str:
        """Системный промпт с актуальной датой и временем."""
        now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z").strip()
        return (
            f"{SETTINGS.system_prompt}\n\n"
            f"{AGENT_INSTRUCTIONS}\n\n"
            f"Текущие дата и время: {now}. "
            "Учитывай это при работе с напоминаниями и относительными датами."
        )

    def _completion(self, messages: List[Dict[str, Any]]) -> tuple[Any, str]:
        """Запрос к основной модели с запасным вариантом при ошибке."""
        models = [SETTINGS.default_model]
        if SETTINGS.fallback_model and SETTINGS.fallback_model != SETTINGS.default_model:
            models.append(SETTINGS.fallback_model)

        last_error: Optional[Exception] = None
        for model in models:
            try:
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=TOOL_SPECS,
                    tool_choice="auto",
                    temperature=SETTINGS.temperature,
                    max_tokens=SETTINGS.max_tokens,
                )
                return response, model
            except Exception as exc:
                last_error = exc
                logger.warning("Модель %s не ответила: %s", model, exc)

        raise AgentError(f"LLM недоступна. Последняя ошибка: {last_error}")

    @staticmethod
    def _parse_arguments(raw: str) -> Dict[str, Any]:
        """Разобрать JSON-аргументы вызова инструмента."""
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            return {"_error": f"Некорректный JSON аргументов: {exc}"}
        if not isinstance(value, dict):
            return {"_error": "Аргументы должны быть JSON-объектом."}
        return value

    def run(self, user_message: str, chat_id: Optional[str] = None) -> AgentReply:
        """Обработать сообщение пользователя и вернуть ответ агента."""
        user_message = (user_message or "").strip()
        if not user_message:
            raise ValueError("Пустое сообщение.")

        history = get_history(chat_id)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            *history,
            {"role": "user", "content": user_message},
        ]

        artifacts: List[str] = []
        last_model = ""

        for round_number in range(1, SETTINGS.max_tool_rounds + 1):
            response, model = self._completion(messages)
            last_model = model
            message = response.choices[0].message

            # Финальный ответ без вызова инструментов
            if not message.tool_calls:
                text = (message.content or "").strip() or "Готово."
                add_message("user", user_message, chat_id)
                add_message("assistant", text, chat_id)
                return AgentReply(text=text, artifacts=artifacts, model=model)

            # Сохраняем ответ модели с запросами инструментов
            messages.append(message.model_dump(exclude_none=True))

            for call in message.tool_calls:
                name = call.function.name
                arguments = self._parse_arguments(call.function.arguments)
                if "_error" in arguments:
                    result: Dict[str, Any] = {"ok": False, "error": arguments["_error"]}
                else:
                    logger.info("Раунд %s: инструмент %s(%s)", round_number, name, arguments)
                    result = execute_tool(name, arguments, context={"chat_id": chat_id or "cli"})

                artifacts.extend(result.get("artifacts", []))
                content = json.dumps(result, ensure_ascii=False, default=str)
                if len(content) > SETTINGS.max_tool_result_chars:
                    content = content[: SETTINGS.max_tool_result_chars] + "… (результат обрезан)"

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": name,
                        "content": content,
                    }
                )

        # Защита от бесконечного цикла
        text = (
            "Не удалось завершить задачу за отведённое число шагов. "
            "Попробуйте уточнить запрос или разбить его на части."
        )
        add_message("user", user_message, chat_id)
        add_message("assistant", text, chat_id)
        return AgentReply(text=text, artifacts=artifacts, model=last_model)
