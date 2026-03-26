"""
Настройка персональных напоминаний.

Пользователь указывает:
- Время напоминания о воде (интервал или конкретные часы)
- Время утреннего опроса о сне
- Время напоминаний о БАДах (дублирует schedule_time на supplement, но через общий UI)

Данные пишутся в таблицу reminders и подхватываются Celery Beat.
"""

import logging
import pytz
from datetime import datetime

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Reminder, ReminderType
from bot.services.user_service import user_service

logger = logging.getLogger(__name__)
router = Router()


class ReminderFSM(StatesGroup):
    waiting_water_times  = State()
    waiting_sleep_time   = State()


# --- Клавиатуры ----------------------------------------------------------

def reminders_menu_kb(reminders: list[Reminder]):
    """Главное меню настроек с текущими временами."""
    water_times  = [r.time_utc for r in reminders if r.reminder_type == ReminderType.WATER and r.is_active]
    sleep_time   = next((r.time_utc for r in reminders if r.reminder_type == ReminderType.SLEEP and r.is_active), None)

    water_label = f"💧 Вода ({', '.join(water_times[:3])})" if water_times else "💧 Вода (не настроено)"
    sleep_label = f"😴 Сон ({sleep_time})"                  if sleep_time  else "😴 Сон (не настроено)"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=water_label,  callback_data="remind:water"))
    builder.row(InlineKeyboardButton(text=sleep_label,  callback_data="remind:sleep"))
    builder.row(InlineKeyboardButton(text="🔕 Отключить все", callback_data="remind:disable_all"))
    return builder.as_markup()


def water_presets_kb():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Каждые 2 часа (8–22)", callback_data="remind:water:auto2h"),
        InlineKeyboardButton(text="Каждые 3 часа (8–22)", callback_data="remind:water:auto3h"),
    )
    builder.row(
        InlineKeyboardButton(text="✏️ Задать вручную",    callback_data="remind:water:manual"),
        InlineKeyboardButton(text="🔕 Отключить",          callback_data="remind:water:off"),
    )
    return builder.as_markup()


def sleep_presets_kb():
    builder = InlineKeyboardBuilder()
    for t in ["07:00", "07:30", "08:00", "08:30", "09:00"]:
        builder.add(InlineKeyboardButton(text=t, callback_data=f"remind:sleep:set:{t}"))
    builder.adjust(5)
    builder.row(InlineKeyboardButton(text="🔕 Отключить", callback_data="remind:sleep:off"))
    return builder.as_markup()


# --- Команда /reminders --------------------------------------------------

@router.message(F.text == "/reminders")
@router.message(F.text == "🔔 Напоминания")
async def reminders_menu(message: Message, db_user, session: AsyncSession):
    reminders = await _get_user_reminders(session, db_user.id)
    await message.answer(
        "🔔 <b>Настройка напоминаний</b>\n\n"
        "Все времена указываются в твоём часовом поясе.\n"
        f"Текущий пояс: <b>{db_user.timezone}</b>",
        parse_mode="HTML",
        reply_markup=reminders_menu_kb(reminders),
    )


# --- Вода ----------------------------------------------------------------

@router.callback_query(F.data == "remind:water")
async def cb_water_remind(call: CallbackQuery):
    await call.message.edit_text(
        "💧 <b>Напоминания о воде</b>\n\n"
        "Выбери удобный режим:",
        parse_mode="HTML",
        reply_markup=water_presets_kb(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("remind:water:auto"))
async def cb_water_auto(call: CallbackQuery, db_user, session: AsyncSession):
    interval = 2 if call.data.endswith("2h") else 3
    tz = pytz.timezone(db_user.timezone)

    # Генерируем времена в UTC
    times_utc = []
    for hour in range(8, 23, interval):
        local_dt = tz.localize(datetime.now().replace(hour=hour, minute=0, second=0))
        utc_dt   = local_dt.utctimetuple()
        times_utc.append(f"{utc_dt.tm_hour:02d}:00")

    await _set_water_reminders(session, db_user.id, times_utc)

    # Строим читаемое локальное расписание
    local_times = [f"{h}:00" for h in range(8, 23, interval)]
    await call.message.edit_text(
        f"✅ Напоминания о воде настроены!\n\n"
        f"Буду напоминать каждые <b>{interval} часа</b>:\n"
        f"{', '.join(local_times)} ({db_user.timezone})",
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "remind:water:manual")
async def cb_water_manual(call: CallbackQuery, state: FSMContext):
    await state.set_state(ReminderFSM.waiting_water_times)
    await call.message.edit_text(
        "💧 Введи время напоминаний через запятую:\n\n"
        "<i>Например: 9:00, 12:00, 15:00, 19:00, 21:00</i>\n\n"
        "Укажи в своём часовом поясе.",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(ReminderFSM.waiting_water_times)
async def step_water_times(message: Message, db_user, session: AsyncSession, state: FSMContext):
    raw = message.text.strip()
    times_local = [t.strip() for t in raw.split(",") if ":" in t]

    if not times_local:
        await message.answer("Укажи времена в формате ЧЧ:ММ через запятую, например: <code>9:00, 13:00, 18:00</code>", parse_mode="HTML")
        return

    tz = pytz.timezone(db_user.timezone)
    times_utc = []
    for t in times_local[:8]:  # максимум 8 напоминаний
        try:
            h, m = map(int, t.split(":"))
            local_dt = tz.localize(datetime.now().replace(hour=h, minute=m, second=0))
            utc_dt   = local_dt.utctimetuple()
            times_utc.append(f"{utc_dt.tm_hour:02d}:{utc_dt.tm_min:02d}")
        except Exception:
            pass

    if not times_utc:
        await message.answer("Не смог распарсить ни одно время. Попробуй: <code>9:00, 13:00</code>", parse_mode="HTML")
        return

    await _set_water_reminders(session, db_user.id, times_utc)
    await state.clear()
    await message.answer(
        f"✅ Напоминания о воде настроены!\n\n"
        f"Расписание: <b>{', '.join(times_local)}</b>",
        parse_mode="HTML",
    )


@router.callback_query(F.data == "remind:water:off")
async def cb_water_off(call: CallbackQuery, db_user, session: AsyncSession):
    await _disable_reminders(session, db_user.id, ReminderType.WATER)
    await call.message.edit_text("🔕 Напоминания о воде отключены.")
    await call.answer()


# --- Сон -----------------------------------------------------------------

@router.callback_query(F.data == "remind:sleep")
async def cb_sleep_remind(call: CallbackQuery):
    await call.message.edit_text(
        "😴 <b>Утренний опрос о сне</b>\n\n"
        "В какое время присылать опрос?",
        parse_mode="HTML",
        reply_markup=sleep_presets_kb(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("remind:sleep:set:"))
async def cb_sleep_set(call: CallbackQuery, db_user, session: AsyncSession):
    local_time = call.data.split(":")[-2] + ":" + call.data.split(":")[-1]
    tz = pytz.timezone(db_user.timezone)

    try:
        h, m = map(int, local_time.split(":"))
        local_dt = tz.localize(datetime.now().replace(hour=h, minute=m, second=0))
        utc_dt   = local_dt.utctimetuple()
        utc_time = f"{utc_dt.tm_hour:02d}:{utc_dt.tm_min:02d}"
    except Exception:
        utc_time = local_time

    await _disable_reminders(session, db_user.id, ReminderType.SLEEP)
    session.add(Reminder(
        user_id=db_user.id,
        reminder_type=ReminderType.SLEEP.value,
        time_utc=utc_time,
        is_active=True,
    ))
    await session.commit()

    await call.message.edit_text(
        f"✅ Утренний опрос о сне — <b>{local_time}</b> ({db_user.timezone})",
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "remind:sleep:off")
async def cb_sleep_off(call: CallbackQuery, db_user, session: AsyncSession):
    await _disable_reminders(session, db_user.id, ReminderType.SLEEP)
    await call.message.edit_text("🔕 Утренний опрос о сне отключён.")
    await call.answer()


# --- Отключить всё -------------------------------------------------------

@router.callback_query(F.data == "remind:disable_all")
async def cb_disable_all(call: CallbackQuery, db_user, session: AsyncSession):
    result = await session.execute(
        select(Reminder).where(Reminder.user_id == db_user.id)
    )
    for r in result.scalars():
        r.is_active = False
    await session.commit()
    await call.message.edit_text("🔕 Все напоминания отключены.")
    await call.answer()


# --- Helpers --------------------------------------------------------------

async def _get_user_reminders(session: AsyncSession, user_id: int) -> list[Reminder]:
    result = await session.execute(
        select(Reminder).where(Reminder.user_id == user_id)
    )
    return result.scalars().all()


async def _set_water_reminders(session: AsyncSession, user_id: int, times_utc: list[str]):
    await _disable_reminders(session, user_id, ReminderType.WATER)
    for t in times_utc:
        session.add(Reminder(
            user_id=user_id,
            reminder_type=ReminderType.WATER.value,
            time_utc=t,
            is_active=True,
        ))
    await session.commit()


async def _disable_reminders(session: AsyncSession, user_id: int, rtype: ReminderType):
    result = await session.execute(
        select(Reminder).where(
            and_(Reminder.user_id == user_id, Reminder.reminder_type == rtype.value)
        )
    )
    for r in result.scalars():
        r.is_active = False
    await session.flush()
