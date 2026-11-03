"""Агентный цикл: LLM ↔ MCP-инструменты ↔ ответ пользователю.

Здесь нет LangChain и «магии»: цикл написан явно, чтобы поведение было
предсказуемым, а расход токенов — ограниченным.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

from .config import Settings
from .history import HistoryStore
from .mcp_client import MCPClient
from .prompts import CONTINUE_PROMPT, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

LeadCallback = Callable[[int | str], Awaitable[None]]

# Инструменты, которые меняют заявку. Если клиент просит её изменить, а модель
# ограничилась обещанием «передам менеджеру», просьба остаётся невыполненной.
LEAD_MUTATION_TOOLS = frozenset(
    {"create_lead", "update_lead", "apply_promo_to_lead", "cancel_lead"}
)

# Обещания, за которыми обязано стоять изменение в базе.
PROMISE_PATTERNS = (
    "передам",
    "передал",
    "передала",
    "передад",
    "сообщу менеджер",
    "уточню у менеджер",
    "менеджер свяжется и исправ",
    "менеджер внес",
    "исправлю заявку",
    "изменю заявку",
    "применю скидку",
    "промокод примен",
    "отменю заявку",
    "удалю заявку",
)

# Подсказка модели: она вызвала не тот инструмент или отделалась обещанием.
MUTATION_NUDGE = (
    "Клиент просил изменить заявку, но в базе ничего не изменилось. "
    "Сначала получи заявки через get_lead_details по контакту клиента, затем "
    "выполни изменение инструментом: скидку — apply_promo_to_lead или "
    "update_lead(promo_code=...), перенос на другой курс — update_lead(course_id=...), "
    "отмену — cancel_lead. Только после этого отвечай клиенту и называй новый итог."
)

# Инструменты напоминаний бот вызывает сам, по таймеру. Модели они не нужны:
# меньше токенов в описании инструментов и нет риска, что модель «запланирует»
# напоминание вместо ответа пользователю.
INTERNAL_TOOLS = frozenset(
    {
        "schedule_lead_reminder",
        "cancel_lead_reminder",
        "get_due_reminders",
        "complete_lead_reminder",
        "get_lead_reminder_status",
    }
)


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
        on_lead_created: LeadCallback | None = None,
    ) -> None:
        self.settings = settings
        self.mcp_client = mcp_client
        self.history = history
        self._on_lead_created = on_lead_created
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
            if tool.name in INTERNAL_TOOLS:
                continue
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
        chat_id: int | str | None = None,
    ) -> str:
        """Выполнить инструмент и ограничить размер результата."""
        if name not in self._tool_names:
            return json.dumps(
                {"ok": False, "error": f"Инструмент {name} не зарегистрирован."},
                ensure_ascii=False,
            )

        result = await self.mcp_client.call_tool(session, name, arguments)
        if name == "create_lead" and chat_id is not None:
            await self._notify_lead_created(chat_id, result)
        if len(result) > self.settings.max_tool_result_chars:
            result = result[: self.settings.max_tool_result_chars] + "… (результат обрезан)"
        return result

    async def _notify_lead_created(self, chat_id: int | str, result: str) -> None:
        """Сообщить подписчику о новой заявке, чтобы снять напоминание."""
        if self._on_lead_created is None:
            return
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, dict) or not data.get("ok") or not data.get("created"):
            return
        try:
            await self._on_lead_created(chat_id)
        except Exception:
            logger.exception("Не удалось обработать событие создания заявки")

    @staticmethod
    def _tool_succeeded(content: str) -> bool:
        """Удался ли вызов инструмента (ответ содержит ``ok: true``)."""
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(data, dict) and bool(data.get("ok"))

    @staticmethod
    def _requests_lead_change(question: str) -> bool:
        """Понять по сообщению, что клиент просит изменить или отменить заявку.

        Ключевые слова грубые, но дешёвые: они лишь включают проверку, что
        просьба действительно исполнена инструментом, а не на словах.
        """
        text = question.lower()
        change_words = (
            "заявк",
            "запис",
            "промокод",
            "скидк",
            "исправ",
            "поправ",
            "измен",
            "поменя",
            "отмен",
            "удал",
            "перенес",
        )
        return any(word in text for word in change_words)

    @staticmethod
    def _promises_change(answer: str) -> bool:
        """Найти в ответе обещание изменить заявку."""
        lowered = answer.lower()
        return any(pattern in lowered for pattern in PROMISE_PATTERNS)

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

            mutated = False
            nudged = False
            for _ in range(max(1, self.settings.max_tool_rounds)):
                response, _model = await self._completion(
                    messages, tool_specs, max_tokens=max_tokens
                )
                if not response.choices:
                    raise AgentError("Модель вернула пустой ответ.")

                message = response.choices[0].message
                tool_calls = getattr(message, "tool_calls", None)
                if not tool_calls:
                    # Просили изменить заявку, а модель ответила обещанием?
                    # Даём один шанс выполнить просьбу инструментом.
                    answer_preview = getattr(message, "content", None) or ""
                    if (
                        not nudged
                        and not mutated
                        and self._requests_lead_change(question)
                        and self._promises_change(answer_preview)
                    ):
                        nudged = True
                        logger.info("Ответ содержит обещание без изменения заявки — повторяем ход")
                        messages.append(message.model_dump(exclude_none=True))
                        messages.append({"role": "system", "content": MUTATION_NUDGE})
                        continue
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
                            session, call.function.name, arguments, chat_id
                        )
                        if call.function.name in LEAD_MUTATION_TOOLS and self._tool_succeeded(
                            content
                        ):
                            mutated = True
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
