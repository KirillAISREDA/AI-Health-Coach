from datetime import date
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import WaterLog
from bot.services.user_service import user_service
from bot.keyboards.main import water_quick_kb

logger = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "💧 Вода")
async def water_menu(message: Message, db_user, session: AsyncSession):
    today_ml = await user_service.get_today_water(session, db_user.id)
    goal_ml = db_user.water_goal_ml or 2000
    pct = min(100, int(today_ml / goal_ml * 100)) if goal_ml else 0

    drops = "💧" * min(10, today_ml // 200)

    await message.answer(
        f"💧 <b>Водный баланс</b>\n\n"
        f"Выпито сегодня: <b>{today_ml} мл</b> из <b>{goal_ml:.0f} мл</b>\n"
        f"{drops or '—'}\n\n"
        f"{'✅ Норма выполнена! 🎉' if pct >= 100 else f'Осталось: <b>{goal_ml - today_ml:.0f} мл</b>'}",
        parse_mode="HTML",
        reply_markup=water_quick_kb(),
    )


@router.callback_query(F.data.startswith("water:") & ~F.data.startswith("water:status"))
async def cb_add_water(call: CallbackQuery, db_user, session: AsyncSession):
    amount_ml = int(call.data.split(":")[1])

    log = WaterLog(user_id=db_user.id, amount_ml=amount_ml, log_date=date.today())
    session.add(log)
    await session.commit()

    today_ml = await user_service.get_today_water(session, db_user.id)
    goal_ml = db_user.water_goal_ml or 2000
    pct = min(100, int(today_ml / goal_ml * 100))

    text = (
        f"✅ +{amount_ml} мл добавлено!\n\n"
        f"💧 Сегодня: <b>{today_ml} мл</b> / {goal_ml:.0f} мл ({pct}%)\n\n"
    )
    if pct >= 100:
        text += "🎉 <b>Норма выполнена! Отличная работа!</b>"
    elif pct >= 75:
        text += "Совсем чуть-чуть осталось — ты молодец 💪"
    elif pct >= 50:
        text += "Половина пути позади! Продолжай 🔥"

    await call.message.edit_text(text, parse_mode="HTML", reply_markup=water_quick_kb())
    await call.answer(f"💧 +{amount_ml} мл!")


@router.callback_query(F.data == "water:status")
async def cb_water_status(call: CallbackQuery, db_user, session: AsyncSession):
    today_ml = await user_service.get_today_water(session, db_user.id)
    goal_ml = db_user.water_goal_ml or 2000
    pct = min(100, int(today_ml / goal_ml * 100)) if goal_ml else 0

    bar_filled = "█" * (pct // 10)
    bar_empty  = "░" * (10 - len(bar_filled))

    await call.message.edit_text(
        f"💧 <b>Статус воды на сегодня</b>\n\n"
        f"[{bar_filled}{bar_empty}] {pct}%\n"
        f"<b>{today_ml} мл</b> из <b>{goal_ml:.0f} мл</b>\n\n"
        f"Осталось: <b>{max(0, goal_ml - today_ml):.0f} мл</b>",
        parse_mode="HTML",
        reply_markup=water_quick_kb(),
    )
    await call.answer()
