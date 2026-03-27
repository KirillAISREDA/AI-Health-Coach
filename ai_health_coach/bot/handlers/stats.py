import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.ai_service import ai_service
from bot.services.user_service import user_service
from bot.keyboards.main import main_menu_kb
from bot.utils.timezone import local_today

logger = logging.getLogger(__name__)
router = Router()


def stats_actions_kb():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📄 PDF-отчёт за неделю", callback_data="report:generate"),
    )
    return builder.as_markup()


# ─── Статистика ───────────────────────────────────────────────────────────────

@router.message(F.text == "📊 Статистика")
async def stats_menu(message: Message, db_user, session: AsyncSession):
    nutrition = await user_service.get_today_nutrition(session, db_user.id, local_today(db_user))
    water_ml  = await user_service.get_today_water(session, db_user.id, local_today(db_user))

    tdee      = db_user.tdee_kcal or 2000
    water_goal = db_user.water_goal_ml or 2000

    cal_pct   = min(100, int(nutrition["calories"] / tdee * 100)) if tdee else 0
    water_pct = min(100, int(water_ml / water_goal * 100)) if water_goal else 0

    def bar(pct):
        filled = "█" * (pct // 10)
        empty  = "░" * (10 - len(filled))
        return f"[{filled}{empty}] {pct}%"

    cal  = nutrition['calories']
    prot = nutrition['protein']
    fat  = nutrition['fat']
    carbs = nutrition['carbs']
    remaining = max(0, tdee - cal)
    pct_remaining = int(remaining / tdee * 100) if tdee else 0

    # Форматирование: целые числа без .0
    def fmt_g(v): return f"{v:.0f}г" if v > 0 else "—"
    def fmt_kcal(v): return f"{v:.0f}" if v > 0 else "0"

    await message.answer(
        f"📊 <b>Сводка за сегодня</b>\n\n"
        f"🔥 <b>Калории</b>\n"
        f"{bar(cal_pct)}\n"
        f"{fmt_kcal(cal)} / {tdee:.0f} ккал"
        + (f"  <i>(осталось {remaining:.0f})</i>" if cal > 0 and remaining > 0 else "") + "\n\n"
        f"🥩 Белки: <b>{fmt_g(prot)}</b>   "
        f"🧈 Жиры: <b>{fmt_g(fat)}</b>   "
        f"🍞 Углеводы: <b>{fmt_g(carbs)}</b>\n\n"
        f"💧 <b>Вода</b>\n"
        f"{bar(water_pct)}\n"
        f"{water_ml} / {water_goal:.0f} мл",
        parse_mode="HTML",
        reply_markup=stats_actions_kb(),
    )


# ─── Профиль ─────────────────────────────────────────────────────────────────

@router.message(F.text == "⚙️ Профиль")
async def profile_menu(message: Message, db_user):
    goal_labels = {
        "lose_weight": "Похудение 🔻",
        "gain_muscle": "Набор массы 💪",
        "maintain": "Поддержание ⚖️",
        "recomposition": "Рекомпозиция 🔄",
    }
    activity_labels = {
        "sedentary": "Сидячий",
        "light": "Низкий",
        "moderate": "Средний",
        "active": "Высокий",
        "very_active": "Очень высокий",
    }

    await message.answer(
        f"⚙️ <b>Твой профиль</b>\n\n"
        f"├ 👤 Пол: <b>{'Мужской' if db_user.gender == 'male' else 'Женский'}</b>\n"
        f"├ 🎂 Возраст: <b>{db_user.age or '—'} лет</b>\n"
        f"├ 📏 Рост: <b>{db_user.height_cm or '—'} см</b>\n"
        f"├ ⚖️ Вес: <b>{db_user.weight_kg or '—'} кг</b>\n"
        f"├ 🎯 Цель: <b>{goal_labels.get(db_user.goal, '—')}</b>\n"
        f"├ 🏃 Активность: <b>{activity_labels.get(db_user.activity_level, '—')}</b>\n"
        f"├ 🔥 TDEE: <b>{int(db_user.tdee_kcal) if db_user.tdee_kcal else '—'} ккал</b>\n"
        f"└ 💧 Норма воды: <b>{int(db_user.water_goal_ml) if db_user.water_goal_ml else '—'} мл</b>\n\n"
        f"Чтобы обновить данные — нажми /start",
        parse_mode="HTML",
    )


# ─── Свободный чат с коучем (fallback handler) ───────────────────────────────

@router.message(F.text & ~F.text.startswith("/"))
async def free_chat(message: Message, db_user, session: AsyncSession):
    """Любое сообщение, не попавшее в другие хэндлеры → идёт к AI-коучу."""

    if not db_user.onboarding_done:
        step = db_user.onboarding_step or ""
        if step and step not in ("start", "gender", "done"):
            await message.answer(
                "⬅️ Похоже, ты в процессе заполнения анкеты.\n"
                "Нажми /start чтобы продолжить с того места."
            )
        else:
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
