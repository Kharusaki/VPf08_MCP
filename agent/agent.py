"""Логика AI-агента: цикл «вопрос -> инструменты -> ответ».

Используем OpenAI Function Calling:
1. Отправляем модели вопрос и список инструментов.
2. Если модель просит инструмент — вызываем его.
3. Возвращаем результат модели и повторяем, пока она не даст финальный ответ.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .config import SETTINGS
from .memory import add_message, get_history
from .tools.registry import BULK_RESULT_TOOLS, TOOL_SPECS, execute_tool

logger = logging.getLogger(__name__)

AGENT_INSTRUCTIONS = """
Ты локальный AI-агент. Твоя задача — помогать пользователю и выполнять задачи,
используя инструменты, а не выдумывая данные.

Правила:
- Отвечай на языке пользователя, структурированно и по делу.
- Для свежих данных (погода, курсы, новости, поиск) обязательно вызывай инструмент.
- Для новостей, текущих событий и любых запросов «найди / поищи / последние / свежие»
  сначала вызывай web_search. Не отвечай по памяти и не подменяй поиск общими словами.
- Для мировых новостей ищи на английском: например,
  «Poland coup October 31 2026» или «world news October 31 2026 key events».
  Регион можно оставить ru-ru — важнее английские ключевые слова и дата.
- После web_search открой 1–3 наиболее релевантные ссылки через read_url и опирайся
  на текст страниц. Не используй http_request для чтения HTML-страниц.
- Если в запросе названа конкретная страна или событие (например, «госпереворот
  в Польше»), сделай отдельный уточняющий поиск по этой теме на английском и открой
  1–2 профильных источника: Reuters, AP, BBC, Euronews, Le Monde, Lenta.ru, RBC,
  Interfax, ТАСС. Не подменяй событие общим «конституционным конфликтом».
- Если результаты поиска нерелевантны, сделай не более 1–2 уточняющих запросов
  (английский язык, site:, точная дата). Не ходи по кругу.
- В ответе указывай конкретные факты, дату и ссылку на источник. Только то, что есть
  в результатах инструментов. Запрещено придумывать события, даты, источники и URL.
- Если просят «3 новости» — дай ровно 3 пункта, без лишних разделов и «воды».
- Если в результатах нет запрошенных фактов — честно скажи «не найдено», перечисли
  поисковые запросы и предложи уточнить тему. Не заполняй пробелы общими фразами.
- Можно вызывать несколько инструментов подряд, если задача составная.
- Если для точного ответа не хватает деталей — задай один короткий уточняющий вопрос.
- Перед запросом курсов валют/криптовалют или точного времени, если пользователь
  не указал часовой пояс в текущем сообщении, сначала спроси: «В каком часовом
  поясе показать время? Например: МСК (UTC+3), UTC или Europe/Moscow».
  Не вызывай инструменты до ответа пользователя.
- Если пользователь уже ответил на вопрос о часовом поясе (например, «МСК»),
  не переспрашивай подтверждение — сразу выполняй исходный запрос из истории.
- Для каждого курса отдельно указывай источник (source) и точное время
  обновления источника (updated_at). Не выноси одно общее время на весь ответ:
  у валют и криптовалют время обновления разное. Не пересчитывай время вручную:
  используй готовые поля updated_at_msk / updated_at_utc (и fetched_at_msk /
  fetched_at_utc, если нужно время запроса). Не путай fetched_at (когда мы
  получили данные) с updated_at (когда их обновил источник). Формат:
  «BTC/RUB: 6 228 032 ₽ — CoinGecko, обновлено 01.11.2026 01:46 МСК
  (22:46 UTC)». Не ограничивайся одной датой и не пиши общие формулировки
  вроде «финансовый API». Если источники расходятся — покажи оба.
- Для напоминаний используй add_reminder; время передавай в ISO 8601 или формате +30m/+2h.
- Не показывай пользователю внутренний JSON и технические детали инструментов.
- Если инструмент вернул ошибку, объясни её простыми словами и предложи альтернативу.
- Ссылки из веб-поиска указывай в ответе.
""".strip()

_NEWS_INTENT = re.compile(
    r"(?i)("
    r"новост|"
    r"(?:последн|свеж|актуальн)\w*\s+(?:новост|событ|информац)|"
    r"что\s+(?:сейчас\s+)?(?:произош|происход|случил)|"
    r"\bсобыти|"
    r"\bпроизошл|"
    r"найди\s+в\s+(?:интернете|сети|duckduckgo)|"
    r"погугли|"
    r"\bnews\b|\blatest\s+news\b|\bbreaking\b"
    r")"
)

_TIME_SENSITIVE = re.compile(
    r"(?i)("
    r"курс|валют|крипт|биткоин|bitcoin|\bbtc\b|\bethereum\b|\beth\b|"
    r"доллар|евро|рубл|\busd\b|\beur\b|\brub\b|"
    r"который\s+час|сколько\s+(?:сейчас\s+)?времени|"
    r"текущее\s+время|время\s+сейчас|точное\s+время"
    r")"
)

_TIMEZONE_MARKER = re.compile(
    r"(?i)("
    r"\b(?:utc|gmt)\s*[+-]\s*\d{1,2}(?::?\d{2})?\b|"
    r"\b(?:utc|gmt|msk)\b|"
    r"\b(?:Europe|Asia|America|Africa|Australia)/[A-Za-z_]+\b|"
    r"\b(?:мск|москв\w*|питер\w*|калининград\w*|екатеринбург\w*|"
    r"новосибирск\w*|красноярск\w*|иркутск\w*|якутск\w*|"
    r"владивосток\w*|магадан\w*|сахалин\w*|камчатк\w*)\b"
    r")"
)

_TIMEZONE_QUESTION = (
    "В каком часовом поясе показать время? "
    "Например: МСК (UTC+3), UTC или Europe/Moscow."
)


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

    def _completion(
        self, messages: List[Dict[str, Any]], tool_choice: Any = "auto"
    ) -> tuple[Any, str]:
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
                    tool_choice=tool_choice,
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

    @staticmethod
    def _needs_web_search(text: str) -> bool:
        """Нужно ли принудительно вызвать поиск для новостного запроса."""
        return bool(_NEWS_INTENT.search(text or ""))

    @staticmethod
    def _needs_timezone(text: str) -> bool:
        """Нужен ли вопрос о часовом поясе перед запросом."""
        return bool(_TIME_SENSITIVE.search(text or "")) and not bool(
            _TIMEZONE_MARKER.search(text or "")
        )

    @staticmethod
    def _is_timezone_answer(text: str, history: List[Dict[str, str]]) -> bool:
        """Является ли сообщение ответом на вопрос о часовом поясе."""
        if not _TIMEZONE_MARKER.search(text or ""):
            return False
        if _TIME_SENSITIVE.search(text or ""):
            return False
        if not history:
            return False
        last = history[-1]
        return (
            last.get("role") == "assistant"
            and "часовом поясе" in last.get("content", "")
        )

    def run(
        self,
        user_message: str,
        chat_id: Optional[str] = None,
        file_task: bool = False,
    ) -> AgentReply:
        """Обработать сообщение пользователя и вернуть ответ агента.

        file_task=True ставит бот, когда сообщение сформировано из присланного
        файла. В этом случае эвристики «спросить часовой пояс» и «обязательно
        искать в интернете» выключены: задача решается по содержимому файла,
        а не по интернету, и переспрашивать про пояс здесь нечего.
        """
        user_message = (user_message or "").strip()
        if not user_message:
            raise ValueError("Пустое сообщение.")

        if not file_task and self._needs_timezone(user_message):
            add_message("user", user_message, chat_id)
            add_message("assistant", _TIMEZONE_QUESTION, chat_id)
            return AgentReply(text=_TIMEZONE_QUESTION, model="timezone-check")

        history = get_history(chat_id)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            *history,
            {"role": "user", "content": user_message},
        ]
        if self._is_timezone_answer(user_message, history):
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Пользователь ответил на вопрос о часовом поясе. "
                        "Не переспрашивай подтверждение — сразу выполни исходный "
                        "запрос из истории и используй этот часовой пояс."
                    ),
                }
            )

        artifacts: List[str] = []
        last_model = ""
        force_search = (not file_task) and self._needs_web_search(user_message)

        for round_number in range(1, SETTINGS.max_tool_rounds + 1):
            tool_choice: Any = "auto"
            if force_search and round_number == 1:
                tool_choice = {"type": "function", "function": {"name": "web_search"}}

            response, model = self._completion(messages, tool_choice)
            last_model = model
            message = response.choices[0].message

            # Финальный ответ без вызова инструментов
            if not message.tool_calls:
                if force_search and round_number == 1:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "Для этого запроса обязателен web_search. "
                                "Не отвечай по памяти — вызови инструмент."
                            ),
                        }
                    )
                    continue
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
                # Текст прочитанного файла нужен модели целиком: при общем лимите
                # в 6000 символов саммари документа строилось бы по первым абзацам.
                limit = (
                    SETTINGS.max_file_result_chars
                    if name in BULK_RESULT_TOOLS
                    else SETTINGS.max_tool_result_chars
                )
                if len(content) > limit:
                    content = content[:limit] + "… (результат обрезан)"

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
