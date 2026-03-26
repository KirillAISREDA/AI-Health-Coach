"""
UserContextMiddleware — при каждом апдейте:
1. Подтягивает (или создаёт) пользователя из БД.
2. Кладёт объекты `user` и `session` в data для хэндлеров.
"""

from typing import Callable, Awaitable, Any
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from bot.services.database import AsyncSessionLocal
from bot.services.user_service import user_service


class UserContextMiddleware(BaseMiddleware):

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_user = None

        if hasattr(event, "from_user") and event.from_user:
            telegram_user = event.from_user
        elif isinstance(event, Update):
            for field in ("message", "callback_query", "my_chat_member"):
                obj = getattr(event, field, None)
                if obj and hasattr(obj, "from_user") and obj.from_user:
                    telegram_user = obj.from_user
                    break

        async with AsyncSessionLocal() as session:
            data["session"] = session

            if telegram_user:
                user = await user_service.get_or_create(
                    session,
                    telegram_id=telegram_user.id,
                    username=telegram_user.username,
                    first_name=telegram_user.first_name,
                )
                data["db_user"] = user
            else:
                data["db_user"] = None

            return await handler(event, data)
