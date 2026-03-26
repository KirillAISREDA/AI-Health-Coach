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
from bot.utils.timezone import resolve_city_to_tz, DEFAULT_TZ

logger = logging.getLogger(__name__)
router = Router()


class OnboardingFSM(StatesGroup):
    waiting_age      = State()
    waiting_height   = State()
    waiting_weight   = State()
    selecting_goal   = State()   # ожидаем выбор цели
    selecting_activity = State() # ожидаем выбор активности
    waiting_allergies  = State()
    waiting_timezone   = State()


# ─── /start ──────────────────────────────────────────────────────────────────

# Маппинг onboarding_step → FSM state для восстановления после /start
STEP_TO_FSM = {
    OnboardingStep.AGE.value:       "OnboardingFSM:waiting_age",
    OnboardingStep.HEIGHT.value:    "OnboardingFSM:waiting_height",
    OnboardingStep.WEIGHT.value:    "OnboardingFSM:waiting_weight",
    OnboardingStep.GOAL.value:      "OnboardingFSM:selecting_goal",
    OnboardingStep.ACTIVITY.value:  "OnboardingFSM:selecting_activity",
    OnboardingStep.ALLERGIES.value: "OnboardingFSM:waiting_allergies",
    OnboardingStep.TIMEZONE.value:  "OnboardingFSM:waiting_timezone",
}


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

    # Восстанавливаем шаг если пользователь уже начал онбординг
    current_step = db_user.onboarding_step
    if current_step and current_step != OnboardingStep.START.value             and current_step != OnboardingStep.GENDER.value:
        await _resume_onboarding(message, db_user, state, current_step)
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


async def _resume_onboarding(
    message: Message, db_user, state: FSMContext, step: str
):
    """Восстанавливает FSM и показывает нужный шаг анкеты."""
    step_messages = {
        OnboardingStep.AGE.value: (
            OnboardingFSM.waiting_age,
            "⬅️ Ты уже начал анкету! Продолжаем.\n\n"
            "<b>Шаг 2/8.</b> Сколько тебе лет?\n\nНапиши цифрой, например: <code>28</code>",
            None,
        ),
        OnboardingStep.HEIGHT.value: (
            OnboardingFSM.waiting_height,
            "⬅️ Продолжаем!\n\n<b>Шаг 3/8.</b> Твой рост в сантиметрах?\n\n"
            "Например: <code>178</code>",
            None,
        ),
        OnboardingStep.WEIGHT.value: (
            OnboardingFSM.waiting_weight,
            "⬅️ Продолжаем!\n\n<b>Шаг 4/8.</b> Текущий вес в кг?\n\n"
            "Например: <code>75.5</code>",
            None,
        ),
        OnboardingStep.GOAL.value: (
            OnboardingFSM.selecting_goal,
            "⬅️ Продолжаем!\n\n<b>Шаг 5/8.</b> Какая у тебя цель?",
            goal_kb(),
        ),
        OnboardingStep.ACTIVITY.value: (
            OnboardingFSM.selecting_activity,
            "⬅️ Продолжаем!\n\n<b>Шаг 6/8.</b> Уровень физической активности:",
            activity_kb(),
        ),
        OnboardingStep.ALLERGIES.value: (
            OnboardingFSM.waiting_allergies,
            "⬅️ Продолжаем!\n\n<b>Шаг 7/8.</b> Есть ли аллергии?\n\n"
            "Перечисли через запятую или нажми «Пропустить».",
            skip_kb("skip_allergies"),
        ),
        OnboardingStep.TIMEZONE.value: (
            OnboardingFSM.waiting_timezone,
            "⬅️ Продолжаем!\n\n<b>Шаг 8/8.</b> Напиши свой город:",
            None,
        ),
    }

    if step not in step_messages:
        # Неизвестный шаг — начинаем сначала
        await message.answer(
            "👋 Привет! Укажи свой пол:",
            reply_markup=gender_kb(),
        )
        return

    fsm_state, text, kb = step_messages[step]
    await state.set_state(fsm_state)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ─── Пол ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("gender:"))
async def cb_gender(call: CallbackQuery, db_user, session: AsyncSession, state: FSMContext):
    gender = call.data.split(":")[1]
    await user_service.update(session, db_user,
                               gender=gender,
                               onboarding_step=OnboardingStep.AGE.value)
    await state.set_state(OnboardingFSM.waiting_age)   # ← КРИТИЧНЫЙ ФИКс
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
    await state.set_state(OnboardingFSM.selecting_goal)
    await message.answer(
        "✅ Записал!\n\n<b>Шаг 5/8.</b> Какая у тебя цель?",
        reply_markup=goal_kb(),
    )


# ─── Цель ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("goal:"), OnboardingFSM.selecting_goal)
async def cb_goal(call: CallbackQuery, db_user, session: AsyncSession, state: FSMContext):
    goal = call.data.split(":")[1]
    await user_service.update(session, db_user, goal=goal,
                               onboarding_step=OnboardingStep.ACTIVITY.value)
    await state.set_state(OnboardingFSM.selecting_activity)
    await call.message.edit_text(
        "✅ Записал!\n\n<b>Шаг 6/8.</b> Уровень физической активности:",
        reply_markup=activity_kb(),
    )
    await call.answer()


# ─── Активность ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("activity:"), OnboardingFSM.selecting_activity)
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

@router.message(OnboardingFSM.waiting_timezone)
async def step_timezone(message: Message, db_user, session: AsyncSession, state: FSMContext):
    city_input = message.text.strip()

    # Сначала быстрый поиск по словарю
    tz = resolve_city_to_tz(city_input)

    if not tz:
        # Не нашли → показываем кнопки с популярными поясами
        await message.answer(
            f"🌍 Не смог определить часовой пояс для «{city_input}».\n\n"
            f"Выбери подходящий UTC-offset:",
            reply_markup=timezone_fallback_kb(),
        )
        return

    user = await user_service.complete_onboarding(session, db_user)
    await session.refresh(user)
    await user_service.update(session, user, timezone=tz)
    await session.refresh(user)
    await state.clear()

    await message.answer(
        f"✅ Часовой пояс определён: <b>{tz}</b>\n\n"
        + _build_onboarding_done_text(user),
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


# ─── Fallback клавиатура для часовых поясов ───────────────────────────────────

def timezone_fallback_kb():
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import InlineKeyboardButton
    builder = InlineKeyboardBuilder()
    zones = [
        ("UTC-5 (Нью-Йорк)",      "America/New_York"),
        ("UTC+0 (Лондон)",         "Europe/London"),
        ("UTC+1 (Берлин/Варшава)", "Europe/Berlin"),
        ("UTC+2 (Киев/Хельсинки)","Europe/Kiev"),
        ("UTC+3 (Москва)",         "Europe/Moscow"),
        ("UTC+4 (Дубай)",          "Asia/Dubai"),
        ("UTC+5:30 (Дели)",        "Asia/Kolkata"),
        ("UTC+7 (Бангкок)",        "Asia/Bangkok"),
        ("UTC+8 (Пекин/СГП)",      "Asia/Shanghai"),
        ("UTC+9 (Токио/Сеул)",     "Asia/Tokyo"),
        ("UTC+10 (Сидней)",        "Australia/Sydney"),
        ("UTC+12 (Окленд)",        "Pacific/Auckland"),
    ]
    for label, tz_val in zones:
        builder.row(InlineKeyboardButton(text=label, callback_data=f"tz_pick:{tz_val}"))
    return builder.as_markup()


@router.callback_query(F.data.startswith("tz_pick:"))
async def cb_tz_pick(call: CallbackQuery, db_user, session: AsyncSession, state: FSMContext):
    tz = call.data.split("tz_pick:", 1)[1]

    # ВАЖНО: call.answer() ПЕРВЫМ — чтобы кнопка не зависала при любой ошибке ниже
    await call.answer()

    user = await user_service.complete_onboarding(session, db_user)
    # refresh нужен чтобы получить актуальный tdee_kcal после complete_onboarding
    await session.refresh(user)
    await user_service.update(session, user, timezone=tz)
    await session.refresh(user)
    await state.clear()

    await call.message.edit_text(
        f"✅ Часовой пояс: <b>{tz}</b>\n\n"
        + _build_onboarding_done_text(user),
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


def _build_onboarding_done_text(user) -> str:
    goal_labels = {
        "lose_weight": "похудение 🔻",
        "gain_muscle": "набор массы 💪",
        "maintain": "поддержание ⚖️",
        "recomposition": "рекомпозиция 🔄",
    }
    # Защита от None — если complete_onboarding не отработал корректно
    tdee   = int(user.tdee_kcal)   if user.tdee_kcal   else "—"
    water  = int(user.water_goal_ml) if user.water_goal_ml else "—"
    weight = f"{user.weight_kg} кг" if user.weight_kg else "—"
    height = f"{user.height_cm} см" if user.height_cm else "—"

    return (
        f"🎉 <b>Анкета заполнена!</b>\n\n"
        f"Вот твои параметры:\n"
        f"├ Вес: <b>{weight}</b>\n"
        f"├ Рост: <b>{height}</b>\n"
        f"├ Цель: <b>{goal_labels.get(user.goal or '', user.goal or '—')}</b>\n"
        f"├ 🔥 Норма калорий: <b>{tdee} ккал/день</b>\n"
        f"└ 💧 Норма воды: <b>{water} мл/день</b>\n\n"
        f"Готов к работе! Выбери, с чего начнём 👇"
    )
