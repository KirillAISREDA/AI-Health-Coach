"""
Admin-хэндлеры — только для admin_ids из .env.

Функции:
- /admin_stats — статистика по боту (юзеры, активность)
- /broadcast — рассылка сообщения всем пользователям
- /admin_user <id> — профиль конкретного пользователя
"""

import logging
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings

logger = logging.getLogger(__name__)
router = Router()


# ── Фильтр: только admin ─────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in settings.get_admin_ids()


class BroadcastFSM(StatesGroup):
    waiting_text    = State()
    waiting_confirm = State()


# ── /admin_stats ─────────────────────────────────────────────────────────────

@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return

    from bot.models import User, FoodLog, WaterLog
    from datetime import date, timedelta

    today    = date.today()
    week_ago = today - timedelta(days=7)

    total_users = await session.scalar(select(func.count(User.id)))
    active_users = await session.scalar(
        select(func.count(func.distinct(FoodLog.user_id))).where(
            FoodLog.meal_date >= week_ago
        )
    )
    onboarded = await session.scalar(
        select(func.count(User.id)).where(User.onboarding_done == True)
    )
    total_food_logs = await session.scalar(
        select(func.count(FoodLog.id)).where(FoodLog.meal_date >= week_ago)
    )
    total_water_logs = await session.scalar(
        select(func.count(WaterLog.id)).where(WaterLog.log_date >= week_ago)
    )

    await message.answer(
        f"📊 <b>Статистика бота</b>\n\n"
        f"<b>Пользователи:</b>\n"
        f"├ Всего: <b>{total_users}</b>\n"
        f"├ Прошли онбординг: <b>{onboarded}</b>\n"
        f"└ Активных за 7 дней: <b>{active_users}</b>\n\n"
        f"<b>За последние 7 дней:</b>\n"
        f"├ Записей питания: <b>{total_food_logs}</b>\n"
        f"└ Записей воды: <b>{total_water_logs}</b>",
        parse_mode="HTML",
    )


# ── /admin_user <id> ─────────────────────────────────────────────────────────

@router.message(Command("admin_user"))
async def cmd_admin_user(message: Message, session: AsyncSession):
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /admin_user <telegram_id>")
        return

    try:
        target_id = int(args[1])
    except ValueError:
        await message.answer("ID должен быть числом")
        return

    from bot.models import User
    user = await session.get(User, target_id)
    if not user:
        await message.answer(f"Пользователь {target_id} не найден")
        return

    goal_labels = {
        "lose_weight": "Похудение", "gain_muscle": "Набор",
        "maintain": "Поддержание", "recomposition": "Рекомпозиция",
    }

    await message.answer(
        f"👤 <b>Профиль {target_id}</b>\n\n"
        f"├ Имя: {user.first_name or '—'} (@{user.username or '—'})\n"
        f"├ Пол: {user.gender or '—'}, {user.age or '—'} лет\n"
        f"├ Вес: {user.weight_kg or '—'} кг, рост: {user.height_cm or '—'} см\n"
        f"├ Цель: {goal_labels.get(user.goal, '—')}\n"
        f"├ TDEE: {int(user.tdee_kcal) if user.tdee_kcal else '—'} ккал\n"
        f"├ TZ: {user.timezone}\n"
        f"└ Онбординг: {'✅' if user.onboarding_done else '❌'}",
        parse_mode="HTML",
    )


# ── /broadcast ───────────────────────────────────────────────────────────────

@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    await state.set_state(BroadcastFSM.waiting_text)
    await message.answer(
        "📢 <b>Рассылка</b>\n\n"
        "Напиши текст сообщения. Поддерживается HTML-разметка.\n"
        "Отправь /cancel для отмены.",
        parse_mode="HTML",
    )


@router.message(BroadcastFSM.waiting_text)
async def step_broadcast_text(message: Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Рассылка отменена.")
        return

    await state.update_data(broadcast_text=message.text)
    await state.set_state(BroadcastFSM.waiting_confirm)
    await message.answer(
        f"📝 <b>Предпросмотр:</b>\n\n{message.text}\n\n"
        f"Отправить всем пользователям?\n"
        f"Напиши <b>ДА</b> для подтверждения или /cancel для отмены.",
        parse_mode="HTML",
    )


@router.message(BroadcastFSM.waiting_confirm)
async def step_broadcast_confirm(
    message: Message,
    bot: Bot,
    session: AsyncSession,
    state: FSMContext,
):
    if message.text == "/cancel" or message.text.upper() != "ДА":
        await state.clear()
        await message.answer("Рассылка отменена.")
        return

    data = await state.get_data()
    text = data["broadcast_text"]
    await state.clear()

    from bot.models import User

    result = await session.execute(
        select(User.id).where(User.onboarding_done == True)
    )
    user_ids = [row[0] for row in result]

    status = await message.answer(f"📤 Рассылаю {len(user_ids)} пользователям...")

    sent = failed = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
            sent += 1
        except Exception as e:
            failed += 1
            logger.warning(f"Broadcast failed for {uid}: {e}")

    await status.edit_text(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"├ Отправлено: <b>{sent}</b>\n"
        f"└ Ошибок: <b>{failed}</b>",
        parse_mode="HTML",
    )
