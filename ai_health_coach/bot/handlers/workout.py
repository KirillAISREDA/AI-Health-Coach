"""
Модуль тренировок.

Флоу:
1. Пользователь открывает меню тренировки.
2. Бот спрашивает о самочувствии (быстрые кнопки).
3. На основе ответа + профиля — генерирует план через GPT-4o.
4. Пользователь отмечает выполнение.

Адаптация нагрузки:
- «Всё хорошо» → полная тренировка по цели
- «Устал / не выспался» → лёгкая восстановительная
- «Болит [место]» → тренировка без нагрузки на больную зону
"""

import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.ai_service import ai_service
from bot.services.user_service import user_service
from bot.models.workout_log import WorkoutLog
from bot.utils.timezone import local_today

logger = logging.getLogger(__name__)
router = Router()


class WorkoutFSM(StatesGroup):
    waiting_injury_detail = State()   # уточнение что болит


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def wellbeing_kb():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💪 Всё отлично!", callback_data="wt:feel:great"),
        InlineKeyboardButton(text="😐 Норм, но устал", callback_data="wt:feel:tired"),
    )
    builder.row(
        InlineKeyboardButton(text="😴 Не выспался", callback_data="wt:feel:sleepy"),
        InlineKeyboardButton(text="🤕 Что-то болит", callback_data="wt:feel:injury"),
    )
    builder.row(
        InlineKeyboardButton(text="😴 Нужен отдых", callback_data="wt:feel:rest"),
    )
    return builder.as_markup()


def equipment_kb():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🏠 Дома (без инвентаря)", callback_data="wt:eq:none"),
        InlineKeyboardButton(text="🏠 Дома (гантели/резинки)", callback_data="wt:eq:home"),
    )
    builder.row(
        InlineKeyboardButton(text="🏋️ Полный зал", callback_data="wt:eq:gym"),
        InlineKeyboardButton(text="🌳 На улице", callback_data="wt:eq:outdoor"),
    )
    return builder.as_markup()


def duration_kb():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚡ 20 мин", callback_data="wt:dur:20"),
        InlineKeyboardButton(text="🕐 40 мин", callback_data="wt:dur:40"),
        InlineKeyboardButton(text="🕑 60 мин", callback_data="wt:dur:60"),
    )
    return builder.as_markup()


def workout_done_kb():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Выполнил!", callback_data="wt:done:full"),
        InlineKeyboardButton(text="⚡ Выполнил частично", callback_data="wt:done:partial"),
        InlineKeyboardButton(text="❌ Не успел", callback_data="wt:done:skip"),
    )
    return builder.as_markup()


# ─── Меню тренировки ─────────────────────────────────────────────────────────

@router.message(F.text == "🏋️ Тренировка")
async def workout_menu(message: Message, db_user, state: FSMContext):
    if not db_user.onboarding_done:
        await message.answer("Сначала заполни анкету! Нажми /start")
        return

    await state.clear()
    await message.answer(
        "🏋️ <b>Тренировка</b>\n\n"
        "Как самочувствие сегодня?",
        parse_mode="HTML",
        reply_markup=wellbeing_kb(),
    )


# ─── Самочувствие ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "wt:feel:rest")
async def feel_rest(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        "😴 <b>День отдыха — это тоже часть тренировочного процесса!</b>\n\n"
        "Сегодня: стретчинг, прогулка, качественный сон 🧘\n\n"
        "_⚠️ Информация носит рекомендательный характер. Проконсультируйся с врачом._",
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "wt:feel:injury")
async def feel_injury(call: CallbackQuery, state: FSMContext):
    await state.set_state(WorkoutFSM.waiting_injury_detail)
    await call.message.edit_text(
        "🤕 Что болит? Напиши, например:\n\n"
        "<i>«колено», «спина», «плечо», «шея»</i>",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(WorkoutFSM.waiting_injury_detail)
async def injury_detail(message: Message, db_user, session: AsyncSession, state: FSMContext):
    await state.update_data(injury=message.text.strip(), feeling="injury")
    await state.set_state(None)
    await message.answer(
        "Понял. Теперь выбери доступный инвентарь:",
        reply_markup=equipment_kb(),
    )


@router.callback_query(F.data.in_({"wt:feel:great", "wt:feel:tired", "wt:feel:sleepy"}))
async def feel_ok(call: CallbackQuery, state: FSMContext):
    feeling = call.data.split(":")[-1]  # great / tired / sleepy
    await state.update_data(feeling=feeling)
    await call.message.edit_text(
        "Отлично! Где тренируемся сегодня?",
        reply_markup=equipment_kb(),
    )
    await call.answer()


# ─── Инвентарь ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("wt:eq:"))
async def select_equipment(call: CallbackQuery, state: FSMContext):
    equipment = call.data.split(":")[-1]
    await state.update_data(equipment=equipment)
    await call.message.edit_text(
        "⏱ Сколько времени есть на тренировку?",
        reply_markup=duration_kb(),
    )
    await call.answer()


# ─── Длительность → генерация плана ──────────────────────────────────────────

FEELING_LABELS = {
    "great":   "отличное, полон энергии",
    "tired":   "немного устал",
    "sleepy":  "не выспался, мало энергии",
    "injury":  "есть дискомфорт/травма",
}

EQUIPMENT_LABELS = {
    "none":    "дома без инвентаря",
    "home":    "дома с гантелями и резинками",
    "gym":     "полностью оборудованный зал",
    "outdoor": "на улице (турник, брусья, асфальт)",
}

GOAL_FOCUS = {
    "lose_weight":    "жиросжигание, кардио + функциональные упражнения",
    "gain_muscle":    "гипертрофия, базовые упражнения с прогрессией нагрузки",
    "maintain":       "поддержание формы, баланс силы и кардио",
    "recomposition":  "рекомпозиция: умеренный дефицит + силовая работа",
}


@router.callback_query(F.data.startswith("wt:dur:"))
async def generate_workout(call: CallbackQuery, db_user, session: AsyncSession, state: FSMContext):
    duration = int(call.data.split(":")[-1])
    fsm_data = await state.get_data()
    feeling   = fsm_data.get("feeling", "great")
    equipment = fsm_data.get("equipment", "none")
    injury    = fsm_data.get("injury", "")
    await state.clear()

    thinking = await call.message.edit_text("🏋️ Составляю план тренировки...")

    profile = user_service.to_profile_dict(db_user)
    goal_focus = GOAL_FOCUS.get(db_user.goal or "maintain", "общая физическая форма")

    # Строим детальный промпт
    injury_clause = ""
    if injury:
        injury_clause = (
            f"\n⚠️ ВАЖНО: у пользователя болит «{injury}». "
            f"ПОЛНОСТЬЮ ИСКЛЮЧИ упражнения с нагрузкой на эту зону. "
            f"Предложи альтернативы."
        )

    adaptation = ""
    if feeling == "tired":
        adaptation = "\nСамочувствие: немного устал. Снизь интенсивность на 20-30%, добавь паузы."
    elif feeling == "sleepy":
        adaptation = "\nСамочувствие: не выспался. Сделай восстановительную тренировку: лёгкая растяжка, мобилити, лёгкое кардио. Без тяжёлых весов."

    prompt = (
        f"Составь персональный план тренировки:\n\n"
        f"👤 Пользователь: {profile.get('gender','?')}, {profile.get('age','?')} лет, "
        f"{profile.get('weight_kg','?')} кг\n"
        f"🎯 Цель: {goal_focus}\n"
        f"⏱ Длительность: {duration} минут\n"
        f"🏠 Инвентарь: {EQUIPMENT_LABELS.get(equipment, equipment)}\n"
        f"😊 Самочувствие: {FEELING_LABELS.get(feeling, feeling)}"
        f"{adaptation}{injury_clause}\n\n"
        f"Формат ответа:\n"
        f"1. Разминка (5-7 мин) — 3-4 упражнения\n"
        f"2. Основная часть — упражнения с подходами/повторениями/временем\n"
        f"3. Заминка (3-5 мин)\n"
        f"4. Краткий совет по питанию после тренировки\n\n"
        f"Будь конкретным: название упражнения, подходы × повторения (или время). "
        f"Используй эмодзи. Максимум 300 слов."
    )

    try:
        response = await ai_service.chat(
            user_id=call.from_user.id,
            user_message=prompt,
            user_profile=profile,
            save_context=True,
        )

        await thinking.edit_text(
            response + "\n\n_⚠️ Информация носит рекомендательный характер. "
                       "Проконсультируйся с врачом._",
            parse_mode="Markdown",
            reply_markup=workout_done_kb(),
        )

        # Сохраняем запись о тренировке
        wlog = WorkoutLog(
            user_id=call.from_user.id,
            feeling=feeling,
            equipment=equipment,
            duration_min=duration,
            injury_zone=injury or None,
            plan_preview=response[:1000],
        )
        session.add(wlog)
        await session.commit()
        # Сохраняем workout_log_id в FSM для отметки выполнения
        await state.update_data(workout_log_id=wlog.id)
    except Exception as e:
        logger.error(f"Workout generation error: {e}")
        await thinking.edit_text("😕 Не удалось составить план. Попробуй снова.")

    await call.answer()


# ─── Отметка выполнения ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("wt:done:"))
async def workout_done(call: CallbackQuery, db_user, session: AsyncSession, state: FSMContext):
    result = call.data.split(":")[-1]

    # Обновляем лог тренировки
    fsm_data = await state.get_data()
    wlog_id  = fsm_data.get("workout_log_id")
    await state.clear()

    if wlog_id:
        from sqlalchemy import select as sa_select
        wlog_res = await session.execute(
            sa_select(WorkoutLog).where(WorkoutLog.id == wlog_id)
        )
        wlog = wlog_res.scalar_one_or_none()
        if wlog:
            wlog.completed = result

    if result == "full":
        # Добавляем 500 мл воды за тренировку автоматически
        from datetime import date
        from bot.models import WaterLog
        water_bonus = WaterLog(
            user_id=db_user.id,
            amount_ml=500,
            log_date=local_today(db_user),
        )
        session.add(water_bonus)
        if wlog_id and wlog:
            wlog.water_bonus_ml = 500
        await session.commit()

        await call.message.edit_text(
            "🔥 <b>Отличная работа!</b>\n\n"
            "Тренировка засчитана. Не забудь восстановиться:\n"
            "└ 💧 +500 мл воды уже добавлено в трекер\n"
            "└ 🥩 Съешь белок в течение 30-60 минут\n"
            "└ 😴 Ляг спать вовремя",
            parse_mode="HTML",
        )
    elif result == "partial":
        await session.commit()
        await call.message.edit_text(
            "⚡ Частичная тренировка тоже считается!\n\n"
            "Главное — стабильность. Завтра добьём 💪",
            parse_mode="HTML",
        )
    else:
        await session.commit()
        await call.message.edit_text(
            "😌 Ничего страшного. Отдых — часть прогресса.\n\n"
            "Завтра вернёмся к тренировкам! 💪",
            parse_mode="HTML",
        )

    await call.answer("Записано!")
