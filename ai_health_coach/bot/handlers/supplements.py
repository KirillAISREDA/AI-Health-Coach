from datetime import date
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Supplement, SupplementLog
from bot.utils.timezone import local_today
from bot.services.ai_service import ai_service
from bot.services.user_service import user_service
from bot.keyboards.main import supplements_menu_kb, supplement_taken_kb, cancel_kb

logger = logging.getLogger(__name__)
router = Router()


class SupFSM(StatesGroup):
    waiting_name     = State()
    waiting_dose     = State()
    waiting_time     = State()


# ─── Меню БАДов ──────────────────────────────────────────────────────────────

@router.message(F.text == "💊 БАДы")
async def sup_menu(message: Message):
    await message.answer(
        "💊 <b>Трекер БАДов</b>\n\n"
        "Добавь свои добавки, и я буду напоминать о приёме.\n"
        "Также могу проверить совместимость.",
        parse_mode="HTML",
        reply_markup=supplements_menu_kb(),
    )


# ─── Список БАДов ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "sup:list")
async def cb_sup_list(call: CallbackQuery, db_user, session: AsyncSession):
    result = await session.execute(
        select(Supplement).where(
            Supplement.user_id == db_user.id,
            Supplement.is_active == True,
        )
    )
    sups = result.scalars().all()

    if not sups:
        await call.message.edit_text(
            "У тебя пока нет добавок. Нажми «Добавить БАД» 👇",
            reply_markup=supplements_menu_kb(),
        )
        await call.answer()
        return

    lines = ["💊 <b>Твои БАДы:</b>\n"]
    for s in sups:
        time_str = f"⏰ {s.schedule_time}" if s.schedule_time else ""
        lines.append(f"• <b>{s.name}</b> {s.dose or ''} {time_str}")

    await call.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=supplements_menu_kb(),
    )
    await call.answer()


# ─── Добавить БАД ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "sup:add")
async def cb_sup_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(SupFSM.waiting_name)
    await call.message.edit_text(
        "➕ <b>Добавление БАД</b>\n\n"
        "Как называется добавка? Например: <code>Магний B6</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await call.answer()


@router.message(SupFSM.waiting_name)
async def step_sup_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(SupFSM.waiting_dose)
    await message.answer(
        "💊 Какая дозировка? Например: <code>400 мг</code>\n\n"
        "Или нажми «Пропустить»",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(SupFSM.waiting_dose)
async def step_sup_dose(message: Message, state: FSMContext):
    await state.update_data(dose=message.text.strip())
    await state.set_state(SupFSM.waiting_time)
    await message.answer(
        "⏰ В какое время принимать? Например: <code>08:00</code>\n\n"
        "Или нажми «Пропустить»",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(SupFSM.waiting_time)
async def step_sup_time(message: Message, db_user, session: AsyncSession, state: FSMContext):
    data = await state.get_data()
    time_str = message.text.strip() if ":" in message.text else None

    sup = Supplement(
        user_id=db_user.id,
        name=data.get("name"),
        dose=data.get("dose"),
        schedule_time=time_str,
    )
    session.add(sup)
    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ <b>{sup.name}</b> добавлен!\n"
        f"{'Напомню в ' + sup.schedule_time if sup.schedule_time else 'Напоминание не настроено.'}",
        parse_mode="HTML",
        reply_markup=supplements_menu_kb(),
    )


# ─── Проверка совместимости ───────────────────────────────────────────────────

@router.callback_query(F.data == "sup:compat")
async def cb_sup_compat(call: CallbackQuery, db_user, session: AsyncSession):
    result = await session.execute(
        select(Supplement).where(
            Supplement.user_id == db_user.id,
            Supplement.is_active == True,
        )
    )
    sups = result.scalars().all()

    if len(sups) < 2:
        await call.message.edit_text(
            "Для проверки совместимости нужно минимум 2 добавки.\n"
            "Сначала добавь БАДы 👇",
            reply_markup=supplements_menu_kb(),
        )
        await call.answer()
        return

    await call.message.edit_text("🔍 Проверяю совместимость...")

    names = [s.name for s in sups]
    response = await ai_service.check_supplement_compatibility(
        user_id=call.from_user.id,
        supplements=names,
    )

    await call.message.edit_text(response, parse_mode="Markdown",
                                  reply_markup=supplements_menu_kb())
    await call.answer()


# ─── Подтверждение приёма (из напоминания Celery) ────────────────────────────

@router.callback_query(F.data.startswith("sup_taken:"))
async def cb_sup_taken(call: CallbackQuery, db_user, session: AsyncSession):
    sup_id = int(call.data.split(":")[1])
    log = SupplementLog(user_id=db_user.id, supplement_id=sup_id,
                        log_date=local_today(db_user), taken=True)
    session.add(log)
    await session.commit()
    await call.message.edit_text("✅ Записал! Молодец, не забываешь о здоровье 💪")
    await call.answer("✅ Принято!")


@router.callback_query(F.data.startswith("sup_skip:"))
async def cb_sup_skip(call: CallbackQuery, db_user, session: AsyncSession):
    sup_id = int(call.data.split(":")[1])
    log = SupplementLog(user_id=db_user.id, supplement_id=sup_id,
                        log_date=local_today(db_user), taken=False)
    session.add(log)
    await session.commit()
    await call.message.edit_text("⏭️ Пропуск записан.")
    await call.answer()
