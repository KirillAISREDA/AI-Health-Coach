"""
Трекер веса.

Команды:
- Кнопка «⚖️ Вес» (добавить в профиль)
- /weight — то же самое

Флоу:
1. Пользователь вводит вес
2. Бот сохраняет, показывает дельту к предыдущему замеру
3. Если изменение > 2 кг — пересчитывает TDEE и спрашивает подтверждение
4. Показывает ASCII-график за последние 8 замеров
"""

import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.weight_log import WeightLog
from bot.services.user_service import user_service, calculate_tdee, calculate_water_goal
from bot.utils.timezone import local_today
from bot.keyboards.main import main_menu_kb

logger = logging.getLogger(__name__)
router = Router()

TDEE_UPDATE_THRESHOLD_KG = 2.0   # пересчитываем TDEE если изменение >= 2 кг


class WeightFSM(StatesGroup):
    waiting_weight = State()
    waiting_note   = State()


# ── Клавиатуры ───────────────────────────────────────────────────────────────

def weight_menu_kb():
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="⚖️ Внести замер",   callback_data="wt_log:enter"),
        InlineKeyboardButton(text="📈 Динамика",        callback_data="wt_log:history"),
    )
    return b.as_markup()


def confirm_tdee_kb(new_tdee: float, new_water: float):
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=f"✅ Обновить TDEE → {int(new_tdee)} ккал",
            callback_data=f"wt_log:update_tdee:{new_tdee}:{new_water}",
        )
    )
    b.row(
        InlineKeyboardButton(text="❌ Оставить прежний", callback_data="wt_log:keep_tdee"),
    )
    return b.as_markup()


# ── Хэндлеры ─────────────────────────────────────────────────────────────────

@router.message(Command("weight"))
@router.message(F.text == "⚖️ Вес")
async def weight_menu(message: Message, db_user, session: AsyncSession):
    last = await _get_last_weight(session, db_user.id)
    last_str = f"{last.weight_kg:.1f} кг ({last.log_date.strftime('%d.%m')})" if last else "нет данных"

    await message.answer(
        f"⚖️ <b>Трекер веса</b>\n\n"
        f"Текущий вес в профиле: <b>{db_user.weight_kg or '—'} кг</b>\n"
        f"Последний замер: <b>{last_str}</b>\n\n"
        f"Регулярные взвешивания помогают точнее рассчитать TDEE 📊",
        parse_mode="HTML",
        reply_markup=weight_menu_kb(),
    )


@router.callback_query(F.data == "wt_log:enter")
async def cb_enter_weight(call: CallbackQuery, state: FSMContext):
    await state.set_state(WeightFSM.waiting_weight)
    await call.message.edit_text(
        "⚖️ Введи свой вес в кг:\n\n"
        "Например: <code>78.5</code>\n\n"
        "<i>Взвешивайся утром натощак для точности</i>",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(WeightFSM.waiting_weight)
async def step_weight_value(
    message: Message,
    db_user,
    session: AsyncSession,
    state: FSMContext,
):
    try:
        weight = float(message.text.strip().replace(",", "."))
        assert 30 <= weight <= 300
    except (ValueError, AssertionError):
        await message.answer(
            "Введи вес корректно, например: <code>78.5</code>",
            parse_mode="HTML",
        )
        return

    await state.update_data(new_weight=weight)
    await state.set_state(WeightFSM.waiting_note)

    # Считаем дельту
    last = await _get_last_weight(session, db_user.id)
    delta_str = ""
    if last:
        delta = weight - last.weight_kg
        arrow = "📈" if delta > 0 else "📉"
        sign  = "+" if delta > 0 else ""
        delta_str = f"\n{arrow} Изменение: <b>{sign}{delta:.1f} кг</b> с последнего замера"

    await message.answer(
        f"✅ Вес <b>{weight:.1f} кг</b> записан!{delta_str}\n\n"
        f"Добавь заметку (необязательно):\n"
        f"<i>«после отпуска», «утром», «после тренировки»...</i>\n\n"
        f"Или /skip чтобы пропустить",
        parse_mode="HTML",
    )


@router.message(WeightFSM.waiting_note)
@router.message(Command("skip"), WeightFSM.waiting_note)
async def step_weight_note(
    message: Message,
    db_user,
    session: AsyncSession,
    state: FSMContext,
):
    fsm_data  = await state.get_data()
    new_weight = fsm_data["new_weight"]
    note = None if message.text.startswith("/skip") else message.text.strip()

    await state.clear()

    # Сохраняем лог
    log = WeightLog(
        user_id=db_user.id,
        weight_kg=new_weight,
        log_date=local_today(db_user),
        note=note,
    )
    session.add(log)

    # Обновляем вес в профиле
    old_weight = db_user.weight_kg or new_weight
    await user_service.update(session, db_user, weight_kg=new_weight)
    await session.commit()

    # Проверяем нужно ли пересчитать TDEE
    delta = abs(new_weight - old_weight)
    if delta >= TDEE_UPDATE_THRESHOLD_KG and all([
        db_user.gender, db_user.age, db_user.height_cm,
        db_user.goal, db_user.activity_level,
    ]):
        new_tdee  = calculate_tdee(
            db_user.gender, db_user.age, db_user.height_cm,
            new_weight, db_user.activity_level, db_user.goal,
        )
        new_water = calculate_water_goal(new_weight)
        old_tdee  = db_user.tdee_kcal or new_tdee

        await message.answer(
            f"⚠️ <b>Изменение веса {delta:.1f} кг</b>\n\n"
            f"Обновить норму калорий?\n"
            f"├ Было: <b>{int(old_tdee)} ккал</b>\n"
            f"└ Станет: <b>{int(new_tdee)} ккал</b>",
            parse_mode="HTML",
            reply_markup=confirm_tdee_kb(new_tdee, new_water),
        )
    else:
        await message.answer(
            f"✅ Вес <b>{new_weight:.1f} кг</b> сохранён!\n"
            + (f"Заметка: <i>{note}</i>" if note else ""),
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )


@router.callback_query(F.data.startswith("wt_log:update_tdee:"))
async def cb_update_tdee(call: CallbackQuery, db_user, session: AsyncSession):
    parts     = call.data.split(":")
    new_tdee  = float(parts[2])
    new_water = float(parts[3])

    await user_service.update(
        session, db_user,
        tdee_kcal=new_tdee,
        water_goal_ml=new_water,
    )

    # Помечаем последний лог как обновивший TDEE
    last = await _get_last_weight(session, db_user.id)
    if last:
        last.tdee_updated = True
        await session.commit()

    await call.message.edit_text(
        f"✅ TDEE обновлён: <b>{int(new_tdee)} ккал/день</b>\n"
        f"💧 Норма воды: <b>{int(new_water)} мл/день</b>",
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "wt_log:keep_tdee")
async def cb_keep_tdee(call: CallbackQuery):
    await call.message.edit_text("✅ Вес сохранён, TDEE оставлен прежним.")
    await call.answer()


# ── История веса ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "wt_log:history")
async def cb_weight_history(call: CallbackQuery, db_user, session: AsyncSession):
    result = await session.execute(
        select(WeightLog)
        .where(WeightLog.user_id == db_user.id)
        .order_by(WeightLog.log_date.desc())
        .limit(10)
    )
    logs = list(reversed(result.scalars().all()))  # хронологически

    if not logs:
        await call.message.edit_text(
            "Ещё нет замеров. Нажми «⚖️ Внести замер»!",
            reply_markup=weight_menu_kb(),
        )
        await call.answer()
        return

    # ASCII-мини-график
    weights   = [l.weight_kg for l in logs]
    min_w, max_w = min(weights), max(weights)
    chart_h   = 6   # строки
    chart_w   = len(logs)

    lines = []
    for row in range(chart_h, -1, -1):
        threshold = min_w + (max_w - min_w) * row / chart_h if max_w > min_w else min_w
        line_chars = []
        for w in weights:
            line_chars.append("█" if w >= threshold else "░")
        lines.append("  " + "".join(line_chars))

    lines.append("  " + "".join(
        l.log_date.strftime("%d")[0] for l in logs
    ))

    chart = "\n".join(lines)

    # Итоговые цифры
    first_w = logs[0].weight_kg
    last_w  = logs[-1].weight_kg
    total_delta = last_w - first_w
    sign = "+" if total_delta > 0 else ""
    trend = "📈 набор" if total_delta > 0 else ("📉 снижение" if total_delta < 0 else "➡️ стабильно")

    rows_text = "\n".join(
        f"  {l.log_date.strftime('%d.%m')}  {l.weight_kg:.1f} кг"
        + (f"  <i>{l.note}</i>" if l.note else "")
        for l in logs[-5:]  # последние 5
    )

    await call.message.edit_text(
        f"📈 <b>Динамика веса</b>\n\n"
        f"<code>{chart}</code>\n\n"
        f"<b>Последние замеры:</b>\n{rows_text}\n\n"
        f"Тренд: {trend} ({sign}{total_delta:.1f} кг за {len(logs)} замеров)",
        parse_mode="HTML",
        reply_markup=weight_menu_kb(),
    )
    await call.answer()


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _get_last_weight(session: AsyncSession, user_id: int):
    result = await session.execute(
        select(WeightLog)
        .where(WeightLog.user_id == user_id)
        .order_by(WeightLog.log_date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
