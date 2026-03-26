"""
Трекер сна.

Флоу:
1. Утром Celery отправляет опрос «Как спал?» (см. celery_app/tasks.py).
2. Пользователь отвечает кнопками: 1–5 звёзд + сколько часов.
3. Данные сохраняются в sleep_logs.
4. Если оценка ≤ 2 — бот предупреждает при следующей тренировке.

Также доступно вручную через меню.
"""

import logging
from datetime import date

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.sleep import SleepLog
from bot.utils.timezone import local_today
from bot.services.ai_service import ai_service
from bot.services.user_service import user_service

logger = logging.getLogger(__name__)
router = Router()


class SleepFSM(StatesGroup):
    waiting_hours   = State()
    waiting_notes   = State()


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def sleep_quality_kb():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="😴 1 — Ужасно",    callback_data="sleep:q:1"),
        InlineKeyboardButton(text="😕 2 — Плохо",     callback_data="sleep:q:2"),
    )
    builder.row(
        InlineKeyboardButton(text="😐 3 — Нормально", callback_data="sleep:q:3"),
        InlineKeyboardButton(text="😊 4 — Хорошо",   callback_data="sleep:q:4"),
    )
    builder.row(
        InlineKeyboardButton(text="🌟 5 — Отлично",  callback_data="sleep:q:5"),
    )
    return builder.as_markup()


def sleep_hours_quick_kb():
    builder = InlineKeyboardBuilder()
    for h in ["5", "5.5", "6", "6.5", "7", "7.5", "8", "8.5", "9+"]:
        builder.add(InlineKeyboardButton(text=h, callback_data=f"sleep:h:{h}"))
    builder.adjust(5)
    return builder.as_markup()


def skip_notes_kb():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Пропустить →", callback_data="sleep:skip_notes"))
    return builder.as_markup()


# ─── Команда / кнопка ─────────────────────────────────────────────────────────

@router.message(F.text == "😴 Сон")
@router.message(F.text == "/sleep")
async def sleep_menu(message: Message, db_user, session: AsyncSession):
    # Показываем сегодняшний лог если есть
    today_log = await _get_today_sleep(session, db_user.id)

    if today_log:
        stars = "⭐" * today_log.quality_score
        await message.answer(
            f"😴 <b>Сон за сегодня уже записан</b>\n\n"
            f"├ Качество: {stars} ({today_log.quality_score}/5)\n"
            f"└ Часов: {today_log.sleep_hours or '—'}\n\n"
            f"Хочешь обновить запись?",
            parse_mode="HTML",
            reply_markup=sleep_quality_kb(),
        )
    else:
        await message.answer(
            "😴 <b>Трекер сна</b>\n\n"
            "Как ты спал этой ночью?",
            parse_mode="HTML",
            reply_markup=sleep_quality_kb(),
        )


# ─── Шаг 1: оценка качества ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("sleep:q:"))
async def cb_sleep_quality(call: CallbackQuery, state: FSMContext):
    score = int(call.data.split(":")[-1])
    await state.update_data(quality_score=score)
    await state.set_state(SleepFSM.waiting_hours)

    quality_comment = {
        1: "Это совсем мало 😔 Постараемся не перегружать тебя сегодня.",
        2: "Бывает. Сегодня лёгкий день 🙏",
        3: "Норм! Двигаемся дальше 💪",
        4: "Хорошо выспался — отличный старт! 🌞",
        5: "Супер сон — будем жечь сегодня 🔥",
    }

    await call.message.edit_text(
        f"{'⭐' * score}  {quality_comment[score]}\n\n"
        f"Сколько часов спал?",
        reply_markup=sleep_hours_quick_kb(),
    )
    await call.answer()


# ─── Шаг 2: количество часов ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("sleep:h:"))
async def cb_sleep_hours(call: CallbackQuery, state: FSMContext):
    hours_str = call.data.split(":")[-1]
    hours = 9.0 if hours_str == "9+" else float(hours_str)
    await state.update_data(sleep_hours=hours)
    await state.set_state(SleepFSM.waiting_notes)

    await call.message.edit_text(
        f"✅ {hours} ч записано.\n\n"
        f"Хочешь добавить заметку? (необязательно)\n"
        f"<i>Например: «просыпался», «тяжело засыпал», «кофе вечером»</i>",
        parse_mode="HTML",
        reply_markup=skip_notes_kb(),
    )
    await call.answer()


@router.message(SleepFSM.waiting_notes)
async def step_sleep_notes(message: Message, db_user, session: AsyncSession, state: FSMContext):
    fsm_data = await state.get_data()
    await _save_sleep_log(session, db_user.id, fsm_data, notes=message.text.strip())
    await state.clear()
    await message.answer(
        await _build_sleep_summary(session, db_user, fsm_data),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "sleep:skip_notes")
async def cb_skip_notes(call: CallbackQuery, db_user, session: AsyncSession, state: FSMContext):
    fsm_data = await state.get_data()
    await _save_sleep_log(session, db_user.id, fsm_data)
    await state.clear()
    await call.message.edit_text(
        await _build_sleep_summary(session, db_user, fsm_data),
        parse_mode="HTML",
    )
    await call.answer()


# ─── Утренний опрос от Celery (вызывается без FSM) ────────────────────────────

async def send_morning_survey(bot, user_id: int, user_name: str):
    """Вызывается из celery task утром."""
    try:
        await bot.send_message(
            user_id,
            f"☀️ Доброе утро, {user_name or 'чемпион'}!\n\n"
            f"😴 Как ты спал этой ночью?",
            reply_markup=sleep_quality_kb(),
        )
    except Exception as e:
        logger.error(f"Morning survey send error for {user_id}: {e}")


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _get_today_sleep(session: AsyncSession, user_id: int, today=None):
    if today is None:
        from datetime import date
        today = date.today()
    result = await session.execute(
        select(SleepLog).where(
            and_(SleepLog.user_id == user_id, SleepLog.log_date == today)
        )
    )
    return result.scalar_one_or_none()


async def _save_sleep_log(
    session: AsyncSession,
    user_id: int,
    fsm_data: dict,
    notes: str = None,
    today=None,
):
    if today is None:
        from datetime import date
        today = date.today()
    # Upsert: удаляем старую запись за сегодня если есть
    existing = await _get_today_sleep(session, user_id, today)
    if existing:
        await session.delete(existing)
        await session.flush()

    log = SleepLog(
        user_id=user_id,
        log_date=today,
        sleep_hours=fsm_data.get("sleep_hours"),
        quality_score=fsm_data.get("quality_score"),
        notes=notes,
        affected_workout=(fsm_data.get("quality_score", 5) <= 2),
    )
    session.add(log)
    await session.commit()


async def _build_sleep_summary(session: AsyncSession, db_user, fsm_data: dict) -> str:
    score = fsm_data.get("quality_score", 3)
    hours = fsm_data.get("sleep_hours", 0)
    stars = "⭐" * score

    advice = ""
    if score <= 2 or hours < 6:
        advice = (
            "\n\n⚠️ <b>Сегодня рекомендую:</b>\n"
            "└ Лёгкая тренировка или отдых\n"
            "└ Больше воды и меньше кофе\n"
            "└ Лечь спать на 30-60 мин раньше обычного"
        )
        # Помечаем в контексте AI — это подберётся при генерации тренировки
        await ai_service.context_store.add_message(
            db_user.id,
            "system",
            f"[СИСТЕМА] Пользователь плохо спал: {hours}ч, оценка {score}/5. "
            f"При генерации тренировки ОБЯЗАТЕЛЬНО предложи лёгкий вариант.",
        )
    elif score >= 4 and hours >= 7:
        advice = "\n\n🔥 Отличное восстановление! Можно работать на полную."

    return (
        f"✅ <b>Сон записан</b>\n\n"
        f"├ Качество: {stars} ({score}/5)\n"
        f"└ Продолжительность: {hours} ч"
        f"{advice}"
    )
