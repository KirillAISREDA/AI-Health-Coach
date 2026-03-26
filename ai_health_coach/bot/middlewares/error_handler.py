"""
ErrorHandlerMiddleware — ловит все необработанные исключения в хэндлерах.

Поведение:
- Логирует полный traceback
- Отправляет пользователю дружелюбное сообщение об ошибке
- В debug-режиме — показывает тип ошибки
- Критические ошибки (DB connection, OpenAI quota) — особые сообщения
"""

import logging
import traceback
from typing import Callable, Awaitable, Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from openai import RateLimitError, APIConnectionError, APIStatusError

from bot.config import settings

logger = logging.getLogger(__name__)


# Тексты для известных ошибок
ERROR_MESSAGES = {
    "rate_limit": (
        "⚠️ OpenAI временно перегружен. "
        "Подожди 30 секунд и попробуй снова."
    ),
    "api_connection": (
        "🌐 Не могу достучаться до AI. "
        "Проверь соединение или попробуй через минуту."
    ),
    "db_error": (
        "💾 Временные проблемы с базой данных. "
        "Уже фиксим — попробуй через минуту."
    ),
    "generic": (
        "😕 Что-то пошло не так. Попробуй ещё раз.\n"
        "Если проблема повторяется — напиши /start"
    ),
}


class ErrorHandlerMiddleware(BaseMiddleware):

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)

        except Exception as e:
            user_id = self._get_user_id(event)
            error_text = self._classify_error(e)

            # Всегда логируем полный traceback
            logger.error(
                f"Unhandled exception for user {user_id}: {type(e).__name__}: {e}\n"
                f"{traceback.format_exc()}"
            )

            # В debug-режиме добавляем тип ошибки
            if settings.debug:
                error_text += f"\n\n<code>{type(e).__name__}: {str(e)[:200]}</code>"

            await self._notify_user(event, error_text)

    @staticmethod
    def _classify_error(e: Exception) -> str:
        if isinstance(e, RateLimitError):
            return ERROR_MESSAGES["rate_limit"]
        if isinstance(e, APIConnectionError):
            return ERROR_MESSAGES["api_connection"]
        if isinstance(e, APIStatusError) and e.status_code == 429:
            return ERROR_MESSAGES["rate_limit"]
        # SQLAlchemy errors
        if "sqlalchemy" in type(e).__module__.lower() or "asyncpg" in str(type(e)).lower():
            return ERROR_MESSAGES["db_error"]
        return ERROR_MESSAGES["generic"]

    @staticmethod
    def _get_user_id(event: TelegramObject) -> int | None:
        if isinstance(event, (Message, CallbackQuery)):
            return event.from_user.id if event.from_user else None
        return None

    @staticmethod
    async def _notify_user(event: TelegramObject, text: str) -> None:
        try:
            if isinstance(event, Message):
                await event.answer(text, parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.answer("Произошла ошибка", show_alert=False)
                if event.message:
                    await event.message.answer(text, parse_mode="HTML")
        except Exception as notify_err:
            logger.error(f"Failed to send error notification: {notify_err}")
