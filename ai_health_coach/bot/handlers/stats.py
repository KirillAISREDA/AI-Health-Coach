import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.ai_service import ai_service
from bot.services.user_service import user_service
from bot.keyboards.main import main_menu_kb

logger = logging.getLogger(__name__)
router = Router()


def stats_actions_kb():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📄 PDF-отчёт за неделю", callback_data="report:generate"),
    )
    return builder.as_markup()


# --- Статистика -----------------------------------------------------------

@router.message(F.text == "📊 Статистика")
async def stats_menu(message: Message, db_user, session: AsyncSession):
    nutrition = await user_service.get_today_nutrition(session, db_user.id)
    water_ml  = await user_service.get_today_water(session, db_user.id)

    tdee      = db_user.tdee_kcal or 2000
    water_goal = db_user.water_goal_ml or 2000

    cal_pct   = min(100, int(nutrition["calories"] / tdee * 100)) if tdee else 0
    water_pct = min(100, int(water_ml / water_goal * 100)) if water_goal else 0

    def bar(pct):
        filled = "█" * (pct // 10)
        empty  = "░" * (10 - len(filled))
        return f"[{filled}{empty}] {pct}%"

    await message.answer(
        f"📊 <b>Сводка за сегодня</b>\n\n"
        f"🔥 <b>Калории</b>\n"
        f"{bar(cal_pct)}\n"
        f"{nutrition['calories']:.0f} / {tdee:.0f} ккал\n\n"
        f"🥩 Белки: <b>{nutrition['protein']:.1f} г</b>   "
        f"🧈 Жиры: <b>{nutrition['fat']:.1f} г</b>   "
        f"🍞 Углеводы: <b>{nutrition['carbs']:.1f} г</b>\n\n"
        f"💧 <b>Вода</b>\n"
        f"{bar(water_pct)}\n"
        f"{water_ml} / {water_goal:.0f} мл",
        parse_mode="HTML",
        reply_markup=stats_actions_kb(),
    )


# --- Свободный чат с коучем (fallback handler) ---------------------------

@router.message(F.text & ~F.text.startswith("/"))
async def free_chat(message: Message, db_user, session: AsyncSession):
    """Любое сообщение, не попавшее в другие хэндлеры -> идёт к AI-коучу."""

    if not db_user.onboarding_done:
        await message.answer(
            "👋 Привет! Сначала давай познакомимся. Нажми /start"
        )
        return

    thinking = await message.answer("🤔 Думаю...")

    try:
        profile = user_service.to_profile_dict(db_user)
        response = await ai_service.chat(
            user_id=message.from_user.id,
            user_message=message.text,
            user_profile=profile,
        )
        await thinking.edit_text(response, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Free chat error: {e}")
        await thinking.edit_text(
            "😕 Что-то пошло не так. Попробуй ещё раз или перефразируй вопрос."
        )
