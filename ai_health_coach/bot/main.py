"""
Точка входа бота.
Поддерживает два режима:
- Polling (dev): python -m bot.main
- Webhook (prod): через uvicorn / gunicorn с FastAPI app
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage

from bot.config import settings
from bot.services.database import init_db
from bot.middlewares.user_context import UserContextMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.middlewares.error_handler import ErrorHandlerMiddleware

# Роутеры (порядок важен: onboarding первым)
from bot.handlers import onboarding, nutrition, water, supplements, stats, workout, sleep, profile, report, reminders, help
from bot.utils.logger import setup_logging

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main():
    setup_logging()

    # ── Инициализация БД ───────────────────────────────────────────────────
    await init_db()
    logger.info("Database initialized")

    # ── Bot + Dispatcher ──────────────────────────────────────────────────
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    # ── Middleware ─────────────────────────────────────────────────────────
    dp.update.middleware(UserContextMiddleware())

    # ── Роутеры (порядок важен: onboarding первым) ─────────────────────────
    dp.include_router(onboarding.router)
    dp.include_router(nutrition.router)
    dp.include_router(water.router)
    dp.include_router(supplements.router)
    dp.include_router(workout.router)
    dp.include_router(sleep.router)
    dp.include_router(profile.router)
    dp.include_router(report.router)
    dp.include_router(reminders.router)
    dp.include_router(help.router)
    dp.include_router(stats.router)   # stats последним — содержит fallback handler

    # ── Отмена ─────────────────────────────────────────────────────────────
    from aiogram import F
    from aiogram.types import CallbackQuery
    from aiogram.fsm.context import FSMContext

    @dp.callback_query(F.data == "cancel")
    async def cb_cancel(call: CallbackQuery, state: FSMContext):
        await state.clear()
        await call.message.edit_text("❌ Отменено.")
        await call.answer()

    # ── Команды в меню Telegram ────────────────────────────────────────────
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start",      description="Главное меню"),
        BotCommand(command="report",     description="PDF-отчёт за неделю"),
        BotCommand(command="reminders",  description="Настроить напоминания"),
        BotCommand(command="sleep",      description="Записать сон"),
        BotCommand(command="help",       description="Справка по командам"),
    ])

    # ── Запуск ────────────────────────────────────────────────────────────
    logger.info("Starting bot in polling mode...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
