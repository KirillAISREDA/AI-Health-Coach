"""
Webhook mode для production.

Запуск:
    uvicorn bot.webhook:app --host 0.0.0.0 --port 8080

Или через Dockerfile CMD:
    CMD ["uvicorn", "bot.webhook:app", "--host", "0.0.0.0", "--port", "8080"]
"""

import logging
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from fastapi import FastAPI, Request, Response

from bot.config import settings
from bot.services.database import init_db
from bot.middlewares.user_context import UserContextMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.middlewares.error_handler import ErrorHandlerMiddleware
from bot.handlers import onboarding, nutrition, water, supplements, stats, workout, sleep, profile, report, reminders, help, weight, admin

logger = logging.getLogger(__name__)

# ── Глобальные объекты ────────────────────────────────────────────────────────
bot: Bot = None
dp: Dispatcher = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown."""
    global bot, dp

    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    # Middleware
    dp.update.middleware(ErrorHandlerMiddleware())
    dp.update.middleware(ThrottlingMiddleware())
    dp.update.middleware(UserContextMiddleware())

    # Роутеры
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
    dp.include_router(weight.router)
    dp.include_router(admin.router)
    dp.include_router(stats.router)

    # Cancel handler
    from aiogram import F
    from aiogram.types import CallbackQuery
    from aiogram.fsm.context import FSMContext

    @dp.callback_query(F.data == "cancel")
    async def cb_cancel(call: CallbackQuery, state: FSMContext):
        await state.clear()
        await call.message.edit_text("❌ Отменено.")
        await call.answer()

    # Устанавливаем webhook
    webhook_url = f"{settings.webhook_host}{settings.webhook_path}"
    await bot.set_webhook(
        url=webhook_url,
        secret_token=settings.webhook_secret,
        drop_pending_updates=True,
    )

    # Команды в меню Telegram
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start",      description="Главное меню"),
        BotCommand(command="report",     description="PDF-отчёт за неделю"),
        BotCommand(command="reminders",  description="Настроить напоминания"),
        BotCommand(command="sleep",      description="Записать сон"),
        BotCommand(command="help",       description="Справка по командам"),
    ])
    logger.info(f"Webhook set: {webhook_url}")

    yield

    # Shutdown
    await bot.delete_webhook()
    await bot.session.close()
    logger.info("Bot stopped")


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


@app.post(settings.webhook_path)
async def telegram_webhook(request: Request) -> Response:
    """Принимает апдейты от Telegram."""
    # Проверка секрета
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if settings.webhook_secret and secret != settings.webhook_secret:
        return Response(status_code=403)

    body = await request.body()
    await dp.feed_raw_update(bot, body)
    return Response(status_code=200)


@app.get("/health")
async def health():
    """Healthcheck для nginx и мониторинга."""
    return {"status": "ok", "bot": "AI Health Coach"}
