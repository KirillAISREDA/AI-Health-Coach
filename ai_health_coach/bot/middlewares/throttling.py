"""
ThrottlingMiddleware — защита от флуда.

Лимиты (настраиваемые):
- Обычные сообщения: 1 запрос / 0.5 сек на пользователя
- Фото: 1 запрос / 3 сек (тяжёлый AI-вызов)
- Callback-кнопки: 1 запрос / 0.3 сек

При превышении: тихо игнорирует (no spam warnings).
После 5 игнорирований подряд — предупреждение.
"""

import time
import logging
from collections import defaultdict
from typing import Callable, Awaitable, Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

logger = logging.getLogger(__name__)

# Лимиты в секундах между запросами
LIMITS = {
    "photo":    3.0,   # фото → GPT Vision
    "message":  0.5,   # текстовые сообщения
    "callback": 0.3,   # нажатия кнопок
}

WARN_AFTER = 5   # предупредить после N тихих блокировок подряд


class ThrottlingMiddleware(BaseMiddleware):

    def __init__(self):
        # user_id → {type: last_call_time}
        self._last_call: dict[int, dict[str, float]] = defaultdict(dict)
        # user_id → счётчик заблокированных подряд
        self._blocked_count: dict[int, int] = defaultdict(int)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = self._get_user_id(event)
        if user_id is None:
            return await handler(event, data)

        event_type = self._get_event_type(event)
        limit = LIMITS.get(event_type, 0.5)

        now = time.monotonic()
        last = self._last_call[user_id].get(event_type, 0.0)

        if now - last < limit:
            # Флуд — увеличиваем счётчик
            self._blocked_count[user_id] += 1
            count = self._blocked_count[user_id]

            if count == WARN_AFTER and isinstance(event, Message):
                await event.answer(
                    "⏳ Притормози немного — я не успеваю обрабатывать столько запросов!\n"
                    "Подожди секунду и попробуй снова."
                )
                logger.warning(f"Throttle warning sent to user {user_id}")

            return  # тихо игнорируем

        # Сбрасываем счётчик и обновляем время
        self._blocked_count[user_id] = 0
        self._last_call[user_id][event_type] = now

        return await handler(event, data)

    @staticmethod
    def _get_user_id(event: TelegramObject) -> int | None:
        if isinstance(event, (Message, CallbackQuery)):
            return event.from_user.id if event.from_user else None
        return None

    @staticmethod
    def _get_event_type(event: TelegramObject) -> str:
        if isinstance(event, CallbackQuery):
            return "callback"
        if isinstance(event, Message) and event.photo:
            return "photo"
        return "message"
