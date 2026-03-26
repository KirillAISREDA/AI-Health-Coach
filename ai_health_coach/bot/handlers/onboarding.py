"""
Онбординг нового пользователя.

Шаги: пол → возраст → рост → вес → цель → активность → аллергии → часовой пояс → готово.
Используем FSM + InlineKeyboard там, где возможен выбор, и text input — для чисел.
"""

import pytz
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import OnboardingStep
from bot.services.user_service import user_service
from bot.keyboards.main import (
    gender_kb, goal_kb, activity_kb, skip_kb, main_menu_kb
)

logger = logging.getLogger(__name__)
router = Router()


class OnboardingFSM(StatesGroup):
    waiting_age     = State()
    waiting_height  = State()
    waiting_weight  = State()
    waiting_allergies = State()
    waiting_timezone = State()


# ─── /start ──────────────────────────────────────────────────────────────────

@router.message(F.text == "/start")
async def cmd_start(message: Message, db_user, session: AsyncSession, state: FSMContext):
    await state.clear()

    if db_user.onboarding_done:
        from bot.keyboards.main import main_menu_kb
        await message.answer(
            f"👋 С возвращением, {db_user.first_name or 'чемпион'}!\n\n"
            f"Я готов помочь. Что делаем?",
            reply_markup=main_menu_kb(),
        )
        return

    await message.answer(
        "👋 Привет! Я — <b>HealthBot</b>, твой персональный AI-коуч.\n\n"
        "Помогу отслеживать питание, тренировки, воду и БАДы.\n\n"
        "Давай начнём с короткой анкеты — займёт 2 минуты ⚡\n\n"
        "<b>Шаг 1/8.</b> Укажи свой пол:",
        parse_mode="HTML",
        reply_markup=gender_kb(),
    )
    await user_service.update(session, db_user, onboarding_step=OnboardingStep.GENDER.value)


# ─── Пол ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("gender:"))
async def cb_gender(call: CallbackQuery, db_user, session: AsyncSession):
    gender = call.data.split(":")[1]
    await user_service.update(session, db_user,
                               gender=gender,
                               onboarding_step=OnboardingStep.AGE.value)
    await call.message.edit_text(
        "✅ Записал!\n\n<b>Шаг 2/8.</b> Сколько тебе лет?\n\n"
        "Напиши цифрой, например: <code>28</code>",
        parse_mode="HTML",
    )
    await call.answer()


# ─── Возраст ──────────────────────────────────────────────────────────────────

@router.message(OnboardingFSM.waiting_age)
async def step_age(message: Message, db_user, session: AsyncSession, state: FSMContext):
    try:
        age = int(message.text.strip())
        assert 10 <= age <= 100
    except (ValueError, AssertionError):
        await message.answer("Введи возраст цифрой от 10 до 100, например: <code>28</code>",
                             parse_mode="HTML")
        return

    await user_service.update(session, db_user, age=age,
                               onboarding_step=OnboardingStep.HEIGHT.value)
    await state.set_state(OnboardingFSM.waiting_height)
    await message.answer(
        "✅ Отлично!\n\n<b>Шаг 3/8.</b> Твой рост в сантиметрах?\n\n"
        "Например: <code>178</code>",
        parse_mode="HTML",
    )


# Запуск FSM после ввода пола (нет FSM на предыдущих шагах — запускаем здесь)
@router.callback_query(F.data.startswith("gender:"))
async def set_fsm_after_gender(call: CallbackQuery, state: FSMContext):
    await state.set_state(OnboardingFSM.waiting_age)


# ─── Рост ─────────────────────────────────────────────────────────────────────

@router.message(OnboardingFSM.waiting_height)
async def step_height(message: Message, db_user, session: AsyncSession, state: FSMContext):
    try:
        height = float(message.text.strip())
        assert 100 <= height <= 250
    except (ValueError, AssertionError):
        await message.answer("Введи рост в см, например: <code>178</code>", parse_mode="HTML")
        return

    await user_service.update(session, db_user, height_cm=height,
                               onboarding_step=OnboardingStep.WEIGHT.value)
    await state.set_state(OnboardingFSM.waiting_weight)
    await message.answer(
        "✅ Записал!\n\n<b>Шаг 4/8.</b> Текущий вес в кг?\n\n"
        "Например: <code>75.5</code>",
        parse_mode="HTML",
    )


# ─── Вес ──────────────────────────────────────────────────────────────────────

@router.message(OnboardingFSM.waiting_weight)
async def step_weight(message: Message, db_user, session: AsyncSession, state: FSMContext):
    try:
        weight = float(message.text.strip().replace(",", "."))
        assert 30 <= weight <= 300
    except (ValueError, AssertionError):
        await message.answer("Введи вес в кг, например: <code>75.5</code>", parse_mode="HTML")
        return

    await user_service.update(session, db_user, weight_kg=weight,
                               onboarding_step=OnboardingStep.GOAL.value)
    await state.clear()
    await message.answer(
        "✅ Записал!\n\n<b>Шаг 5/8.</b> Какая у тебя цель?",
        reply_markup=goal_kb(),
    )


# ─── Цель ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("goal:"))
async def cb_goal(call: CallbackQuery, db_user, session: AsyncSession):
    goal = call.data.split(":")[1]
    await user_service.update(session, db_user, goal=goal,
                               onboarding_step=OnboardingStep.ACTIVITY.value)
    await call.message.edit_text(
        "✅ Записал!\n\n<b>Шаг 6/8.</b> Уровень физической активности:",
        reply_markup=activity_kb(),
    )
    await call.answer()


# ─── Активность ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("activity:"))
async def cb_activity(call: CallbackQuery, db_user, session: AsyncSession, state: FSMContext):
    activity = call.data.split(":")[1]
    await user_service.update(session, db_user, activity_level=activity,
                               onboarding_step=OnboardingStep.ALLERGIES.value)
    await state.set_state(OnboardingFSM.waiting_allergies)
    await call.message.edit_text(
        "✅ Записал!\n\n<b>Шаг 7/8.</b> Есть ли у тебя аллергии или продукты, "
        "которые ты не ешь?\n\n"
        "Перечисли через запятую или нажми «Пропустить».",
        reply_markup=skip_kb("skip_allergies"),
    )
    await call.answer()


@router.message(OnboardingFSM.waiting_allergies)
async def step_allergies(message: Message, db_user, session: AsyncSession, state: FSMContext):
    await user_service.update(session, db_user,
                               allergies=message.text.strip(),
                               onboarding_step=OnboardingStep.TIMEZONE.value)
    await state.set_state(OnboardingFSM.waiting_timezone)
    await message.answer(
        "✅ Записал!\n\n<b>Шаг 8/8.</b> Напиши свой город, чтобы я присылал "
        "напоминания в правильное время.\n\nНапример: <code>Москва</code> или <code>Киев</code>",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "skip_allergies")
async def skip_allergies(call: CallbackQuery, db_user, session: AsyncSession, state: FSMContext):
    await user_service.update(session, db_user,
                               onboarding_step=OnboardingStep.TIMEZONE.value)
    await state.set_state(OnboardingFSM.waiting_timezone)
    await call.message.edit_text(
        "✅ Хорошо!\n\n<b>Шаг 8/8.</b> Напиши свой город, чтобы я присылал "
        "напоминания в правильное время.\n\nНапример: <code>Москва</code>",
        parse_mode="HTML",
    )
    await call.answer()


# ─── Часовой пояс ─────────────────────────────────────────────────────────────

CITY_TO_TZ = {
    "москва": "Europe/Moscow", "спб": "Europe/Moscow", "санкт-петербург": "Europe/Moscow",
    "киев": "Europe/Kiev", "минск": "Europe/Minsk",
    "алматы": "Asia/Almaty", "астана": "Asia/Almaty", "нур-султан": "Asia/Almaty",
    "ташкент": "Asia/Tashkent", "баку": "Asia/Baku", "тбилиси": "Asia/Tbilisi",
    "берлин": "Europe/Berlin", "вена": "Europe/Vienna", "варшава": "Europe/Warsaw",
    "лондон": "Europe/London", "нью-йорк": "America/New_York",
    "dubai": "Asia/Dubai", "дубай": "Asia/Dubai",
}


@router.message(OnboardingFSM.waiting_timezone)
async def step_timezone(message: Message, db_user, session: AsyncSession, state: FSMContext):
    city = message.text.strip().lower()
    tz = CITY_TO_TZ.get(city, "Europe/Moscow")

    user = await user_service.complete_onboarding(session, db_user)
    await user_service.update(session, user, timezone=tz)
    await state.clear()

    goal_labels = {
        "lose_weight": "похудение 🔻",
        "gain_muscle": "набор массы 💪",
        "maintain": "поддержание ⚖️",
        "recomposition": "рекомпозиция 🔄",
    }

    await message.answer(
        f"🎉 <b>Анкета заполнена!</b>\n\n"
        f"Вот твои параметры:\n"
        f"├ Вес: <b>{user.weight_kg} кг</b>\n"
        f"├ Рост: <b>{user.height_cm} см</b>\n"
        f"├ Цель: <b>{goal_labels.get(user.goal, user.goal)}</b>\n"
        f"├ 🔥 Норма калорий: <b>{int(user.tdee_kcal)} ккал/день</b>\n"
        f"└ 💧 Норма воды: <b>{int(user.water_goal_ml)} мл/день</b>\n\n"
        f"Готов к работе! Выбери, с чего начнём 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )
