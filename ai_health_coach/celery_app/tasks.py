"""
Celery tasks — напоминания и фоновые задачи.

Задачи:
- send_water_reminders: каждые 2 часа, если норма не выполнена
- send_supplement_reminders: по расписанию БАДов
- send_weekly_digest: каждое воскресенье утром
"""

import asyncio
import logging
from datetime import datetime, date
import pytz

from celery import Celery
from celery.schedules import crontab

from bot.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery("healthbot", broker=settings.celery_broker_url)
celery_app.conf.update(
    result_backend=settings.celery_result_backend,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        # Утренний опрос о сне — 8:00 UTC (≈ 11:00 МСК)
        "morning-sleep-survey": {
            "task": "celery_app.tasks.send_morning_sleep_survey",
            "schedule": crontab(hour=8, minute=0),
        },
        # Напоминания о воде — каждые 2 часа с 8 до 22
        "water-reminders-every-2h": {
            "task": "celery_app.tasks.send_water_reminders",
            "schedule": crontab(minute=0, hour="8,10,12,14,16,18,20,22"),
        },
        # Напоминания о БАДах — каждые 30 минут (проверяем расписание)
        "supplement-reminders": {
            "task": "celery_app.tasks.send_supplement_reminders",
            "schedule": crontab(minute="*/30"),
        },
        # Еженедельный дайджест — воскресенье 09:00 UTC
        "weekly-digest": {
            "task": "celery_app.tasks.send_weekly_digest",
            "schedule": crontab(hour=9, minute=0, day_of_week="sunday"),
        },
    },
)


def run_async(coro):
    """Запускает async-функцию в синхронном контексте Celery."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="celery_app.tasks.send_morning_sleep_survey")
def send_morning_sleep_survey():
    """Утренний опрос о качестве сна."""
    run_async(_send_morning_sleep_survey_async())


async def _send_morning_sleep_survey_async():
    from aiogram import Bot
    from sqlalchemy import select
    from bot.models import User
    from bot.models.sleep import SleepLog
    from bot.services.database import AsyncSessionLocal
    from bot.handlers.sleep import send_morning_survey
    import pytz

    bot = Bot(token=settings.bot_token)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.onboarding_done == True)
        )
        users = result.scalars().all()

        for user in users:
            try:
                tz = pytz.timezone(user.timezone)
                local_hour = datetime.now(tz).hour
                if not (7 <= local_hour <= 10):
                    continue
                existing = await session.execute(
                    select(SleepLog).where(
                        SleepLog.user_id == user.id,
                        SleepLog.log_date == date.today(),
                    )
                )
                if existing.scalar_one_or_none():
                    continue
                await send_morning_survey(bot, user.id, user.first_name)
            except Exception as e:
                logger.error(f"Morning survey error for {user.id}: {e}")

    await bot.session.close()


@celery_app.task(name="celery_app.tasks.send_water_reminders")
def send_water_reminders():
    """Присылает напоминание о воде пользователям, не выполнившим норму."""
    run_async(_send_water_reminders_async())


async def _send_water_reminders_async():
    from aiogram import Bot
    from sqlalchemy import select
    from bot.models import User, WaterLog
    from bot.services.database import AsyncSessionLocal

    bot = Bot(token=settings.bot_token)
    now_hour = datetime.utcnow().hour

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.onboarding_done == True)
        )
        users = result.scalars().all()

        for user in users:
            try:
                # Локальный час пользователя
                tz = pytz.timezone(user.timezone)
                local_hour = datetime.now(tz).hour
                if not (8 <= local_hour <= 22):
                    continue

                today_ml = 0
                water_res = await session.execute(
                    select(WaterLog).where(
                        WaterLog.user_id == user.id,
                        WaterLog.log_date == date.today(),
                    )
                )
                logs = water_res.scalars().all()
                today_ml = sum(l.amount_ml for l in logs)

                goal = user.water_goal_ml or 2000
                if today_ml >= goal:
                    continue  # Норма выполнена

                remaining = int(goal - today_ml)
                from aiogram.utils.keyboard import InlineKeyboardBuilder
                from aiogram.types import InlineKeyboardButton
                builder = InlineKeyboardBuilder()
                builder.row(
                    InlineKeyboardButton(text="💧 +250 мл", callback_data="water:250"),
                    InlineKeyboardButton(text="💧 +500 мл", callback_data="water:500"),
                )

                await bot.send_message(
                    user.id,
                    f"💧 <b>Напоминание о воде</b>\n\n"
                    f"Сегодня ещё нужно выпить <b>{remaining} мл</b>.\n"
                    f"Выпито: {today_ml} / {goal:.0f} мл",
                    parse_mode="HTML",
                    reply_markup=builder.as_markup(),
                )
            except Exception as e:
                logger.error(f"Water reminder error for user {user.id}: {e}")

    await bot.session.close()


@celery_app.task(name="celery_app.tasks.send_supplement_reminders")
def send_supplement_reminders():
    """Напоминания о приёме БАДов по расписанию."""
    run_async(_send_supplement_reminders_async())


async def _send_supplement_reminders_async():
    from aiogram import Bot
    from sqlalchemy import select, and_
    from bot.models import User, Supplement, SupplementLog
    from bot.services.database import AsyncSessionLocal
    from bot.keyboards.main import supplement_taken_kb

    bot = Bot(token=settings.bot_token)
    now_utc = datetime.utcnow()
    current_time = now_utc.strftime("%H:%M")
    # Проверяем ±15 минут от текущего времени
    current_hour = now_utc.hour
    current_minute = now_utc.minute

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Supplement).where(
                Supplement.is_active == True,
                Supplement.schedule_time != None,
            )
        )
        sups = result.scalars().all()

        for sup in sups:
            try:
                sup_time = sup.schedule_time  # "08:00"
                sup_hour, sup_minute = map(int, sup_time.split(":"))

                # Проверяем попадание в окно ±14 минут
                diff = abs((current_hour * 60 + current_minute) - (sup_hour * 60 + sup_minute))
                if diff > 14:
                    continue

                # Не посылать повторно если уже записан лог за сегодня
                existing = await session.execute(
                    select(SupplementLog).where(
                        and_(
                            SupplementLog.supplement_id == sup.id,
                            SupplementLog.log_date == date.today(),
                        )
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                await bot.send_message(
                    sup.user_id,
                    f"💊 <b>Время принять {sup.name}!</b>\n"
                    f"{sup.dose or ''}",
                    parse_mode="HTML",
                    reply_markup=supplement_taken_kb(sup.id),
                )
            except Exception as e:
                logger.error(f"Supplement reminder error for sup {sup.id}: {e}")

    await bot.session.close()


@celery_app.task(name="celery_app.tasks.send_weekly_digest")
def send_weekly_digest():
    """Еженедельный дайджест по воскресеньям."""
    run_async(_send_weekly_digest_async())


async def _send_weekly_digest_async():
    from aiogram import Bot
    from sqlalchemy import select
    from bot.models import User
    from bot.services.database import AsyncSessionLocal
    from bot.services.ai_service import ai_service
    from bot.services.user_service import user_service

    bot = Bot(token=settings.bot_token)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.onboarding_done == True)
        )
        users = result.scalars().all()

        for user in users:
            try:
                week_stats = await user_service.get_week_stats(session, user.id)
                profile = user_service.to_profile_dict(user)

                # Обогащаем статистику целями
                week_stats["protein_goal_g"] = (profile.get("tdee_kcal", 2000) * 0.3) / 4 * 7
                week_stats["water_goal_ml_total"] = (profile.get("water_goal_ml", 2000)) * 7

                digest = await ai_service.generate_weekly_digest(
                    user_id=user.id,
                    week_stats=week_stats,
                    user_profile=profile,
                )

                await bot.send_message(
                    user.id,
                    f"📊 <b>Твой еженедельный дайджест</b>\n\n{digest}",
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Weekly digest error for user {user.id}: {e}")

    await bot.session.close()
