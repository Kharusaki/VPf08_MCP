"""Агентный цикл: LLM ↔ MCP-инструменты ↔ ответ пользователю.

Здесь нет LangChain и «магии»: цикл написан явно, чтобы поведение было
предсказуемым, а расход токенов — ограниченным.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from openai import AsyncOpenAI

from .config import Settings
from .history import HistoryStore
from .mcp_client import MCPClient
from .prompts import CONTINUE_PROMPT, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class AgentError(RuntimeError):
    """Понятная пользователю ошибка агента (LLM или пустой ответ)."""


class SalesAgent:
    """Консультант, который получает факты только через MCP-инструменты."""

    def __init__(
        self,
        settings: Settings,
        mcp_client: MCPClient,
        history: HistoryStore,
        openai_client: AsyncOpenAI | None = None,
    ) -> None:
        self.settings = settings
        self.mcp_client = mcp_client
        self.history = history
        if openai_client is not None:
            self._client: AsyncOpenAI | None = openai_client
        elif settings.openai_api_key:
            self._client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                timeout=float(settings.request_timeout),
            )
        else:
            self._client = None
        self._tool_specs: list[dict[str, Any]] | None = None
        self._tool_names: set[str] = set()

    def _system_prompt(self) -> str:
        """Добавить к промпту актуальную дату, чтобы модель не путала сезоны."""
        today = datetime.now().strftime("%d.%m.%Y")
        return f"{SYSTEM_PROMPT}\n\nСегодня {today}."

    async def _ensure_tools(self, session: Any) -> list[dict[str, Any]]:
        """Загрузить инструменты MCP один раз и перевести их в формат OpenAI."""
        if self._tool_specs is not None:
            return self._tool_specs

        tools = await self.mcp_client.list_tools(session)
        specs: list[dict[str, Any]] = []
        for tool in tools:
            schema = getattr(tool, "inputSchema", None) or {
                "type": "object",
                "properties": {},
            }
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": getattr(tool, "description", "") or "",
                        "parameters": schema,
                    },
                }
            )

        if not specs:
            raise AgentError("MCP-сервер не вернул ни одного инструмента.")

        self._tool_specs = specs
        self._tool_names = {item["function"]["name"] for item in specs}
        logger.info("Загружено MCP-инструментов: %d", len(specs))
        return specs

    async def _completion(
        self,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]],
        allow_tools: bool = True,
        max_tokens: int | None = None,
    ) -> tuple[Any, str]:
        """Запрос к основной модели с запасной моделью при ошибке."""
        if self._client is None:
            raise AgentError("В .env не задан OPENAI_API_KEY.")

        models = [self.settings.default_model]
        if (
            self.settings.fallback_model
            and self.settings.fallback_model != self.settings.default_model
        ):
            models.append(self.settings.fallback_model)

        # max_tokens приходит из режима /length; без него действует .env.
        effective_max_tokens = (
            self.settings.max_tokens if max_tokens is None else int(max_tokens)
        )
        kwargs: dict[str, Any] = {
            "temperature": self.settings.temperature,
            "max_tokens": max(1, effective_max_tokens),
        }
        if allow_tools and tool_specs:
            kwargs["tools"] = tool_specs
            kwargs["tool_choice"] = "auto"

        last_error: Exception | None = None
        for model in models:
            try:
                response = await self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **kwargs,
                )
                return response, model
            except Exception as exc:  # сеть, лимиты, неверная модель и т.п.
                last_error = exc
                logger.warning("Модель %s не ответила: %s", model, exc)

        raise AgentError("Не удалось получить ответ от LLM.") from last_error

    @staticmethod
    def _parse_arguments(raw: str) -> tuple[dict[str, Any], str | None]:
        """Разобрать JSON-аргументы вызова инструмента."""
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            return {}, f"Некорректный JSON аргументов: {exc}"
        if not isinstance(value, dict):
            return {}, "Аргументы инструмента должны быть JSON-объектом."
        return value, None

    async def _execute_tool(
        self,
        session: Any,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Выполнить инструмент и ограничить размер результата."""
        if name not in self._tool_names:
            return json.dumps(
                {"ok": False, "error": f"Инструмент {name} не зарегистрирован."},
                ensure_ascii=False,
            )

        result = await self.mcp_client.call_tool(session, name, arguments)
        if len(result) > self.settings.max_tool_result_chars:
            result = result[: self.settings.max_tool_result_chars] + "… (результат обрезан)"
        return result

    def _remember(self, chat_id: int | str, question: str, answer: str) -> None:
        """Сохранить успешный ход диалога, не ломая ответ при ошибке диска."""
        try:
            self.history.append(chat_id, "user", question)
            self.history.append(chat_id, "assistant", answer)
        except OSError:
            logger.exception("Не удалось сохранить историю диалога")

    @staticmethod
    def _join_parts(left: str, right: str) -> str:
        """Склеить две части ответа, не ломая пробелы и знаки препинания."""
        left = left.rstrip()
        right = right.lstrip()
        if not left:
            return right
        if not right:
            return left
        opening = {" ", "\n", "\t", "(", "[", "{", "«", "“", "-"}
        closing = {" ", "\n", "\t", ".", ",", ";", ":", "!", "?", ")", "]", "}", "»", "”", "\"", "'", "…"}
        if left[-1] in opening or right[0] in closing:
            return left + right
        return f"{left} {right}"

    async def _finalize_answer(
        self,
        chat_id: int | str,
        question: str,
        response: Any,
        messages: list[dict[str, Any]],
        tool_specs: list[dict[str, Any]],
        max_tokens: int | None,
    ) -> str:
        """Достроить ответ, если модель остановилась по лимиту токенов.

        Провайдеры сообщают об этом через ``finish_reason="length"``. В этом
        случае просим модель продолжить с места обрыва и склеиваем части.
        Число продолжений ограничено, чтобы расход токенов оставался конечным.
        """
        if not response.choices:
            raise AgentError("Модель вернула пустой ответ.")

        choice = response.choices[0]
        message = choice.message
        answer = (getattr(message, "content", None) or "").strip()
        if not answer:
            raise AgentError("Модель вернула пустой ответ.")

        continuations = 0
        limit = max(0, int(self.settings.max_continuations))
        while (
            getattr(choice, "finish_reason", None) == "length"
            and continuations < limit
        ):
            messages.append(
                {
                    "role": "assistant",
                    "content": getattr(message, "content", None) or answer,
                }
            )
            messages.append({"role": "user", "content": CONTINUE_PROMPT})
            try:
                response, _model = await self._completion(
                    messages,
                    tool_specs,
                    allow_tools=False,
                    max_tokens=max_tokens,
                )
            except AgentError:
                logger.warning("Не удалось продолжить оборванный ответ")
                break

            if not response.choices:
                break
            choice = response.choices[0]
            message = choice.message
            part = (getattr(message, "content", None) or "").strip()
            if not part:
                break
            answer = self._join_parts(answer, part)
            continuations += 1
            logger.info("Ответ продолжен: %d из %d", continuations, limit)

        if getattr(choice, "finish_reason", None) == "length":
            logger.warning(
                "Ответ достиг лимита длины после %d продолжений",
                continuations,
            )
            if not answer.endswith(("…", "...")):
                answer = f"{answer.rstrip()} …"

        self._remember(chat_id, question, answer)
        return answer

    async def run(
        self,
        chat_id: int | str,
        user_message: str,
        max_tokens: int | None = None,
    ) -> str:
        """Обработать сообщение пользователя и вернуть финальный текст."""
        question = (user_message or "").strip()
        if not question:
            raise AgentError("Пустое сообщение.")

        async with self.mcp_client.connect() as session:
            tool_specs = await self._ensure_tools(session)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": self._system_prompt()},
                *self.history.get(chat_id),
                {"role": "user", "content": question},
            ]

            for _ in range(max(1, self.settings.max_tool_rounds)):
                response, _model = await self._completion(
                    messages, tool_specs, max_tokens=max_tokens
                )
                if not response.choices:
                    raise AgentError("Модель вернула пустой ответ.")

                message = response.choices[0].message
                tool_calls = getattr(message, "tool_calls", None)
                if not tool_calls:
                    return await self._finalize_answer(
                        chat_id,
                        question,
                        response,
                        messages,
                        tool_specs,
                        max_tokens,
                    )

                messages.append(message.model_dump(exclude_none=True))

                for call in tool_calls:
                    arguments, error = self._parse_arguments(call.function.arguments)
                    if error:
                        content = json.dumps(
                            {"ok": False, "error": error},
                            ensure_ascii=False,
                        )
                    else:
                        logger.info(
                            "MCP-вызов %s(%s)",
                            call.function.name,
                            arguments,
                        )
                        content = await self._execute_tool(
                            session, call.function.name, arguments
                        )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.function.name,
                            "content": content,
                        }
                    )

            # Страховка от бесконечного цикла: просим финальный ответ без инструментов.
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Лимит вызовов инструментов исчерпан. Сформулируй ответ "
                        "по уже полученным данным и не вызывай инструменты."
                    ),
                }
            )
            response, _model = await self._completion(
                messages,
                tool_specs,
                allow_tools=False,
                max_tokens=max_tokens,
            )
            if not response.choices:
                raise AgentError("Не удалось завершить диалог за отведённое число шагов.")
            return await self._finalize_answer(
                chat_id,
                question,
                response,
                messages,
                tool_specs,
                max_tokens,
            )
