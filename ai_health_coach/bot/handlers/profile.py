"""
Модуль профиля.

Позволяет пользователю обновить отдельные параметры:
- вес (самое частое — раз в неделю)
- цель
- уровень активности
- аллергии
- часовой пояс

При изменении любого из этих параметров автоматически пересчитывается TDEE и норма воды.
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.user_service import user_service, calculate_tdee, calculate_water_goal
from bot.keyboards.main import goal_kb, activity_kb, main_menu_kb
from bot.utils.timezone import resolve_city_to_tz

logger = logging.getLogger(__name__)
router = Router()


class ProfileFSM(StatesGroup):
    waiting_new_weight    = State()
    waiting_new_allergies = State()
    waiting_new_timezone  = State()


# ─── Клавиатура меню профиля ─────────────────────────────────────────────────

def profile_menu_kb():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚖️ Обновить вес",      callback_data="profile:weight"),
        InlineKeyboardButton(text="🎯 Изменить цель",      callback_data="profile:goal"),
    )
    builder.row(
        InlineKeyboardButton(text="🏃 Уровень активности", callback_data="profile:activity"),
        InlineKeyboardButton(text="🚫 Аллергии",           callback_data="profile:allergies"),
    )
    builder.row(
        InlineKeyboardButton(text="🌍 Часовой пояс",       callback_data="profile:timezone"),
        InlineKeyboardButton(text="📊 Мои показатели",     callback_data="profile:stats"),
    )
    return builder.as_markup()


# ─── Главная страница профиля ────────────────────────────────────────────────

@router.message(F.text == "⚙️ Профиль")
async def profile_menu(message: Message, db_user):
    goal_labels = {
        "lose_weight":    "Похудение 🔻",
        "gain_muscle":    "Набор массы 💪",
        "maintain":       "Поддержание ⚖️",
        "recomposition":  "Рекомпозиция 🔄",
    }
    activity_labels = {
        "sedentary":   "Сидячий 🪑",
        "light":       "Низкий 🚶",
        "moderate":    "Средний 🏃",
        "active":      "Высокий 🔥",
        "very_active": "Профи 🏆",
    }

    await message.answer(
        f"⚙️ <b>Твой профиль</b>\n\n"
        f"├ 👤 Пол: <b>{'Мужской' if db_user.gender == 'male' else 'Женский'}</b>\n"
        f"├ 🎂 Возраст: <b>{db_user.age or '—'} лет</b>\n"
        f"├ 📏 Рост: <b>{db_user.height_cm or '—'} см</b>\n"
        f"├ ⚖️ Вес: <b>{db_user.weight_kg or '—'} кг</b>\n"
        f"├ 🎯 Цель: <b>{goal_labels.get(db_user.goal, '—')}</b>\n"
        f"├ 🏃 Активность: <b>{activity_labels.get(db_user.activity_level, '—')}</b>\n"
        f"├ 🔥 TDEE: <b>{int(db_user.tdee_kcal) if db_user.tdee_kcal else '—'} ккал/день</b>\n"
        f"├ 💧 Норма воды: <b>{int(db_user.water_goal_ml) if db_user.water_goal_ml else '—'} мл/день</b>\n"
        f"└ 🚫 Аллергии: <b>{db_user.allergies or 'нет'}</b>\n\n"
        f"Что хочешь обновить?",
        parse_mode="HTML",
        reply_markup=profile_menu_kb(),
    )


# ─── Обновление веса ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "profile:weight")
async def cb_update_weight(call: CallbackQuery, state: FSMContext):
    await state.set_state(ProfileFSM.waiting_new_weight)
    await call.message.edit_text(
        "⚖️ Введи новый вес в кг:\n\n"
        "Например: <code>73.5</code>",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(ProfileFSM.waiting_new_weight)
async def step_new_weight(message: Message, db_user, session: AsyncSession, state: FSMContext):
    try:
        weight = float(message.text.strip().replace(",", "."))
        assert 30 <= weight <= 300
    except (ValueError, AssertionError):
        await message.answer("Введи вес корректно, например: <code>73.5</code>", parse_mode="HTML")
        return

    old_weight = db_user.weight_kg or weight
    diff = weight - old_weight
    diff_str = f"+{diff:.1f}" if diff > 0 else f"{diff:.1f}"

    # Пересчитываем TDEE и воду
    if all([db_user.gender, db_user.age, db_user.height_cm, db_user.goal, db_user.activity_level]):
        new_tdee  = calculate_tdee(
            gender=db_user.gender, age=db_user.age,
            height_cm=db_user.height_cm, weight_kg=weight,
            activity_level=db_user.activity_level, goal=db_user.goal,
        )
        new_water = calculate_water_goal(weight)
        await user_service.update(
            session, db_user,
            weight_kg=weight,
            tdee_kcal=new_tdee,
            water_goal_ml=new_water,
        )
        await state.clear()
        await message.answer(
            f"✅ <b>Вес обновлён!</b>\n\n"
            f"├ Было: {old_weight:.1f} кг → Стало: <b>{weight:.1f} кг</b>  ({diff_str} кг)\n"
            f"├ 🔥 Новый TDEE: <b>{int(new_tdee)} ккал/день</b>\n"
            f"└ 💧 Новая норма воды: <b>{int(new_water)} мл/день</b>",
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )
    else:
        await user_service.update(session, db_user, weight_kg=weight)
        await state.clear()
        await message.answer(
            f"✅ Вес обновлён: <b>{weight:.1f} кг</b>",
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )


# ─── Обновление цели ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "profile:goal")
async def cb_update_goal(call: CallbackQuery):
    await call.message.edit_text(
        "🎯 Выбери новую цель:",
        reply_markup=goal_kb(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("goal:"))
async def cb_goal_selected(call: CallbackQuery, db_user, session: AsyncSession):
    # Игнорируем если онбординг ещё не закончен (там свой обработчик)
    if not db_user.onboarding_done:
        return

    goal = call.data.split(":")[1]
    goal_labels = {
        "lose_weight":    "Похудение 🔻",
        "gain_muscle":    "Набор массы 💪",
        "maintain":       "Поддержание ⚖️",
        "recomposition":  "Рекомпозиция 🔄",
    }

    # Пересчитываем TDEE с новой целью
    if all([db_user.gender, db_user.age, db_user.height_cm, db_user.weight_kg, db_user.activity_level]):
        new_tdee = calculate_tdee(
            gender=db_user.gender, age=db_user.age,
            height_cm=db_user.height_cm, weight_kg=db_user.weight_kg,
            activity_level=db_user.activity_level, goal=goal,
        )
        await user_service.update(session, db_user, goal=goal, tdee_kcal=new_tdee)
        await call.message.edit_text(
            f"✅ Цель обновлена: <b>{goal_labels.get(goal, goal)}</b>\n\n"
            f"🔥 Новый TDEE: <b>{int(new_tdee)} ккал/день</b>",
            parse_mode="HTML",
            reply_markup=profile_menu_kb(),
        )
    else:
        await user_service.update(session, db_user, goal=goal)
        await call.message.edit_text(
            f"✅ Цель обновлена: <b>{goal_labels.get(goal, goal)}</b>",
            parse_mode="HTML",
            reply_markup=profile_menu_kb(),
        )
    await call.answer()


# ─── Обновление активности ───────────────────────────────────────────────────

@router.callback_query(F.data == "profile:activity")
async def cb_update_activity(call: CallbackQuery):
    await call.message.edit_text(
        "🏃 Выбери уровень активности:",
        reply_markup=activity_kb(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("activity:"))
async def cb_activity_selected(call: CallbackQuery, db_user, session: AsyncSession):
    if not db_user.onboarding_done:
        return

    activity = call.data.split(":")[1]
    activity_labels = {
        "sedentary":   "Сидячий 🪑",
        "light":       "Низкий 🚶",
        "moderate":    "Средний 🏃",
        "active":      "Высокий 🔥",
        "very_active": "Профи 🏆",
    }

    if all([db_user.gender, db_user.age, db_user.height_cm, db_user.weight_kg, db_user.goal]):
        new_tdee = calculate_tdee(
            gender=db_user.gender, age=db_user.age,
            height_cm=db_user.height_cm, weight_kg=db_user.weight_kg,
            activity_level=activity, goal=db_user.goal,
        )
        await user_service.update(session, db_user, activity_level=activity, tdee_kcal=new_tdee)
        await call.message.edit_text(
            f"✅ Активность обновлена: <b>{activity_labels.get(activity, activity)}</b>\n\n"
            f"🔥 Новый TDEE: <b>{int(new_tdee)} ккал/день</b>",
            parse_mode="HTML",
            reply_markup=profile_menu_kb(),
        )
    else:
        await user_service.update(session, db_user, activity_level=activity)
        await call.message.edit_text(
            f"✅ Активность: <b>{activity_labels.get(activity, activity)}</b>",
            parse_mode="HTML",
            reply_markup=profile_menu_kb(),
        )
    await call.answer()


# ─── Обновление аллергий ─────────────────────────────────────────────────────

@router.callback_query(F.data == "profile:allergies")
async def cb_update_allergies(call: CallbackQuery, state: FSMContext):
    await state.set_state(ProfileFSM.waiting_new_allergies)
    await call.message.edit_text(
        "🚫 Перечисли аллергии или продукты, которые не ешь.\n\n"
        "<i>Например: «глютен, молоко, орехи»</i>\n\n"
        "Или напиши «нет» чтобы очистить список.",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(ProfileFSM.waiting_new_allergies)
async def step_new_allergies(message: Message, db_user, session: AsyncSession, state: FSMContext):
    text = message.text.strip()
    allergies = None if text.lower() in ("нет", "no", "-", "") else text
    await user_service.update(session, db_user, allergies=allergies)
    await state.clear()
    await message.answer(
        f"✅ Аллергии обновлены: <b>{allergies or 'нет'}</b>",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


# ─── Обновление часового пояса ───────────────────────────────────────────────

# ─── Обновление часового пояса ───────────────────────────────────────────────


@router.callback_query(F.data == "profile:timezone")
async def cb_update_timezone(call: CallbackQuery, state: FSMContext):
    await state.set_state(ProfileFSM.waiting_new_timezone)
    await call.message.edit_text(
        "🌍 Напиши свой город для определения часового пояса:\n\n"
        "<i>Москва, Алматы, Берлин, Дубай, Токио, Лондон...</i>\n\n"
        "Поддерживается 80+ городов.",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(ProfileFSM.waiting_new_timezone)
async def step_new_timezone(message: Message, db_user, session: AsyncSession, state: FSMContext):
    city_input = message.text.strip()
    tz = resolve_city_to_tz(city_input)

    if not tz:
        await message.answer(
            f"🌍 Не нашёл часовой пояс для «{city_input}».\n\n"
            f"Попробуй написать по-другому, например:\n"
            f"<i>Москва, Берлин, Дубай, Токио, Лондон, Нью-Йорк</i>",
            parse_mode="HTML",
        )
        return

    await user_service.update(session, db_user, timezone=tz)
    await state.clear()
    await message.answer(
        f"✅ Часовой пояс обновлён: <b>{tz}</b>",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


# ─── Показатели прогресса ────────────────────────────────────────────────────

@router.callback_query(F.data == "profile:stats")
async def cb_profile_stats(call: CallbackQuery, db_user, session: AsyncSession):
    week_stats = await user_service.get_week_stats(session, db_user.id)
    tdee = db_user.tdee_kcal or 2000
    water_goal = db_user.water_goal_ml or 2000

    cal_avg   = week_stats["total_calories"] / 7
    water_avg = week_stats["total_water_ml"] / 7

    cal_pct   = min(100, int(cal_avg / tdee * 100)) if tdee else 0
    water_pct = min(100, int(water_avg / water_goal * 100)) if water_goal else 0

    def bar(pct):
        filled = "█" * (pct // 10)
        empty  = "░" * (10 - len(filled))
        return f"[{filled}{empty}] {pct}%"

    await call.message.edit_text(
        f"📈 <b>Средние показатели за 7 дней</b>\n\n"
        f"🔥 Калории (avg/день):\n"
        f"{bar(cal_pct)}\n"
        f"{cal_avg:.0f} / {tdee:.0f} ккал\n\n"
        f"💧 Вода (avg/день):\n"
        f"{bar(water_pct)}\n"
        f"{water_avg:.0f} / {water_goal:.0f} мл\n\n"
        f"🥩 Белок всего: <b>{week_stats['total_protein_g']:.0f} г</b>",
        parse_mode="HTML",
        reply_markup=profile_menu_kb(),
    )
    await call.answer()
