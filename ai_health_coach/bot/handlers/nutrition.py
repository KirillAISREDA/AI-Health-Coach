"""
Модуль питания v2.

АРХИТЕКТУРА:
- 1 вызов AI на приём пищи (фото или текст) — JSON mode
- Сохранение в БД СРАЗУ, до взаимодействия с юзером
- Опциональная коррекция веса inline-кнопками (чистая математика, без AI)
- Fallback на локальную базу продуктов при сбое AI

FSM-состояния (оба — необязательные, данные уже сохранены):
- waiting_food_text: юзер нажал «записать текстом»
- correcting_weight: юзер нажал «уточнить вес» на записи
"""

import logging
import io
import re

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, PhotoSize,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.ai_service import ai_service
from bot.services.user_service import user_service
from bot.services.food_database import food_db
from bot.utils.timezone import local_today
from bot.models import FoodLog
from bot.keyboards.main import nutrition_menu_kb, cancel_kb, main_menu_kb

logger = logging.getLogger(__name__)
router = Router()


# ─── FSM (минимум состояний) ─────────────────────────────────────────────────

class NutritionFSM(StatesGroup):
    waiting_food_text  = State()  # юзер нажал «записать текстом»
    correcting_weight  = State()  # юзер нажал «уточнить вес»


# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def nutrition_result_kb(log_id: int, confidence: str = "medium") -> InlineKeyboardMarkup:
    """Кнопки после сохранения записи."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Ок", callback_data=f"food_ok:{log_id}"),
        InlineKeyboardButton(text="⚖️ Уточнить вес", callback_data=f"food_fix:{log_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Удалить запись", callback_data=f"food_del:{log_id}"),
    )
    return builder.as_markup()


# ─── Форматирование ─────────────────────────────────────────────────────────

def format_nutrition_message(data: dict, source: str = "ai") -> str:
    """
    Форматирует структурированный dict в читаемое сообщение.
    source: "ai" | "fallback"
    """
    items = data.get("items", [])
    total = data.get("total", {})
    comment = data.get("comment", "")
    confidence = data.get("confidence", "medium")

    lines = ["🥗 <b>Анализ приёма пищи</b>"]
    if source == "fallback":
        lines.append("<i>(оценка по базе продуктов — AI временно недоступен)</i>")
    lines.append("")

    for item in items:
        name = item.get("name", "?")
        w = item.get("weight_g", 0)
        cal = item.get("calories", 0)
        lines.append(f"  ├ {name}: ~{w:.0f}г ({cal:.0f} ккал)")

    t_cal = total.get("calories", 0)
    t_p   = total.get("protein", 0)
    t_f   = total.get("fat", 0)
    t_c   = total.get("carbs", 0)

    lines.append("")
    lines.append(
        f"📊 <b>Итого: {t_cal:.0f} ккал</b>\n"
        f"  ├ 🥩 Белки: <b>{t_p:.1f}г</b>\n"
        f"  ├ 🧈 Жиры: <b>{t_f:.1f}г</b>\n"
        f"  └ 🍞 Углеводы: <b>{t_c:.1f}г</b>"
    )

    if confidence == "low" or source == "fallback":
        lines.append(
            "\n⚠️ <i>Оценка приблизительная — нажми «Уточнить вес» для точности</i>"
        )

    if comment:
        lines.append(f"\n💬 {comment}")

    lines.append("\n✅ <i>Записал в дневник!</i>")
    return "\n".join(lines)


# ─── Сохранение в БД ─────────────────────────────────────────────────────────

async def save_food_log(
    session: AsyncSession,
    db_user,
    data: dict,
    raw_input: str,
    is_photo: bool = False,
    photo_file_id: str | None = None,
) -> FoodLog:
    """Сохраняет запись о приёме пищи из структурированного dict."""
    total = data.get("total", {})

    food_log = FoodLog(
        user_id=db_user.id,
        raw_input=raw_input[:500],
        description=data.get("comment", "")[:500],
        is_photo=is_photo,
        photo_file_id=photo_file_id,
        weight_g=total.get("weight_g"),
        weight_confirmed=False,
        meal_date=local_today(db_user),
        calories=total.get("calories"),
        protein_g=total.get("protein"),
        fat_g=total.get("fat"),
        carbs_g=total.get("carbs"),
    )
    session.add(food_log)
    await session.commit()
    await session.refresh(food_log)

    logger.info(
        f"Food saved: user={db_user.id} cal={total.get('calories', 0):.0f} "
        f"log_id={food_log.id}"
    )
    return food_log


# ─── Общий обработчик: AI → fallback → ошибка ───────────────────────────────

async def _process_food(
    session: AsyncSession,
    db_user,
    thinking_msg: Message,
    raw_input: str,
    photo_bytes: bytes | None = None,
    text_input: str | None = None,
    is_photo: bool = False,
    photo_file_id: str | None = None,
) -> bool:
    """
    Универсальный обработчик: пробует AI → fallback → ошибка.
    Возвращает True если данные сохранены, False если ничего не получилось.
    """
    profile = user_service.to_profile_dict(db_user)
    user_id = db_user.id

    # ── Шаг 1: Пробуем AI (JSON mode, 1 вызов) ──
    data = None
    try:
        data = await ai_service.analyze_food_complete(
            user_id=user_id,
            photo_bytes=photo_bytes,
            text_input=text_input,
            user_profile=profile,
        )
    except Exception as e:
        logger.warning(f"AI call failed for user {user_id}: {e}")

    if data and data.get("total", {}).get("calories"):
        food_log = await save_food_log(
            session, db_user, data,
            raw_input=raw_input,
            is_photo=is_photo,
            photo_file_id=photo_file_id,
        )
        confidence = data.get("confidence", "medium")
        await thinking_msg.edit_text(
            format_nutrition_message(data, source="ai"),
            parse_mode="HTML",
            reply_markup=nutrition_result_kb(food_log.id, confidence),
        )
        return True

    # ── Шаг 2: Fallback — локальная база (только для текста) ──
    if text_input:
        logger.info(f"AI failed, trying local DB: {text_input[:80]}")
        try:
            fallback_data = food_db.estimate_from_text(text_input)

            if fallback_data and fallback_data.get("total", {}).get("calories"):
                food_log = await save_food_log(
                    session, db_user, fallback_data,
                    raw_input=raw_input,
                    is_photo=is_photo,
                    photo_file_id=photo_file_id,
                )
                await thinking_msg.edit_text(
                    format_nutrition_message(fallback_data, source="fallback"),
                    parse_mode="HTML",
                    reply_markup=nutrition_result_kb(food_log.id, "low"),
                )
                return True
        except Exception as e:
            logger.error(f"Fallback DB error: {e}")

    return False


# ─── Меню питания ────────────────────────────────────────────────────────────

@router.message(F.text == "🥗 Питание")
async def nutrition_menu(message: Message):
    await message.answer(
        "🥗 <b>Модуль питания</b>\n\n"
        "Сфотографируй блюдо или опиши текстом — я посчитаю КБЖУ.",
        parse_mode="HTML",
        reply_markup=nutrition_menu_kb(),
    )


@router.callback_query(F.data == "food:text")
async def cb_food_text(call: CallbackQuery, state: FSMContext):
    await state.set_state(NutritionFSM.waiting_food_text)
    await call.message.edit_text(
        "✏️ Напиши, что ты съел. Можно в свободной форме:\n\n"
        "<i>«Два варёных яйца, тост с маслом и чай с сахаром»</i>\n\n"
        "Я сам разберусь с составом и порциями 👇",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await call.answer()


# ─── Обработка фото ─────────────────────────────────────────────────────────

@router.message(F.photo)
async def handle_food_photo(
    message: Message,
    bot: Bot,
    db_user,
    session: AsyncSession,
    state: FSMContext,
):
    """Фото еды → 1 вызов AI (JSON) → сохранение → кнопки."""

    if not db_user.onboarding_done:
        await message.answer("Сначала давай заполним анкету! Нажми /start")
        return

    photo: PhotoSize = message.photo[-1]
    caption = message.caption or ""
    thinking_msg = await message.answer("📸 Анализирую фото...")

    try:
        file = await bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        photo_bytes = buf.getvalue()
    except Exception as e:
        logger.error(f"Photo download error: {e}")
        await thinking_msg.edit_text(
            "😕 Не удалось скачать фото. Попробуй ещё раз."
        )
        return

    saved = await _process_food(
        session, db_user, thinking_msg,
        raw_input=f"[фото] {caption}" if caption else "[фото]",
        photo_bytes=photo_bytes,
        text_input=caption if caption else None,
        is_photo=True,
        photo_file_id=photo.file_id,
    )

    if not saved:
        await thinking_msg.edit_text(
            "😕 Не удалось распознать блюдо. Попробуй описать текстом:\n\n"
            "<i>«Куриная котлета 100г, капуста 80г, спаржа 60г»</i>",
            parse_mode="HTML",
        )
        await state.set_state(NutritionFSM.waiting_food_text)


# ─── Обработка текста ────────────────────────────────────────────────────────

@router.message(NutritionFSM.waiting_food_text)
async def handle_food_text(
    message: Message,
    db_user,
    session: AsyncSession,
    state: FSMContext,
):
    """Текст → AI (JSON) → fallback на базу → ошибка."""

    thinking_msg = await message.answer("🧠 Анализирую...")

    saved = await _process_food(
        session, db_user, thinking_msg,
        raw_input=message.text,
        text_input=message.text,
    )

    if saved:
        await state.clear()
    else:
        await state.clear()
        await thinking_msg.edit_text(
            "😕 Не удалось распознать продукты. Попробуй написать проще:\n\n"
            "<i>«куриная грудка 200г и рис 150г»</i>",
            parse_mode="HTML",
        )


# ─── Inline: подтверждение ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("food_ok:"))
async def cb_food_ok(call: CallbackQuery):
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("👍 Записано!")


# ─── Inline: удаление ───────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("food_del:"))
async def cb_food_del(call: CallbackQuery, db_user, session: AsyncSession):
    try:
        log_id = int(call.data.split(":")[1])
        food_log = await session.get(FoodLog, log_id)

        if food_log and food_log.user_id == db_user.id:
            await session.delete(food_log)
            await session.commit()
            await call.message.edit_text(
                "🗑 <i>Запись удалена из дневника.</i>",
                parse_mode="HTML",
            )
        else:
            await call.answer("Запись не найдена")
    except Exception as e:
        logger.error(f"Delete error: {e}")
        await call.answer("Ошибка при удалении")


# ─── Inline: коррекция веса ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("food_fix:"))
async def cb_food_fix(call: CallbackQuery, state: FSMContext):
    log_id = int(call.data.split(":")[1])
    await state.update_data(fix_log_id=log_id)
    await state.set_state(NutritionFSM.correcting_weight)

    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer(
        "⚖️ Введи общий вес порции в граммах:\n\n"
        "Например: <code>240</code>",
        parse_mode="HTML",
    )
    await call.answer()


@router.message(NutritionFSM.correcting_weight)
async def handle_weight_correction(
    message: Message,
    db_user,
    session: AsyncSession,
    state: FSMContext,
):
    """Пересчёт КБЖУ пропорционально новому весу. 0 вызовов AI."""

    match = re.search(r'(\d+[.,]?\d*)', message.text.strip())
    if not match:
        await message.answer(
            "⚖️ Введи число, например: <code>240</code>",
            parse_mode="HTML",
        )
        return

    new_weight = float(match.group(1).replace(",", "."))
    if not (10 <= new_weight <= 5000):
        await message.answer(
            "⚖️ Вес должен быть от 10 до 5000 г.",
            parse_mode="HTML",
        )
        return

    fsm_data = await state.get_data()
    log_id = fsm_data.get("fix_log_id")

    if not log_id:
        await state.clear()
        await message.answer("Запись не найдена. Попробуй записать заново.")
        return

    try:
        food_log = await session.get(FoodLog, log_id)

        if not food_log or food_log.user_id != db_user.id:
            await state.clear()
            await message.answer("Запись не найдена.")
            return

        # Пропорциональный пересчёт
        old_weight = food_log.weight_g or new_weight
        if old_weight > 0 and old_weight != new_weight:
            ratio = new_weight / old_weight
            food_log.calories  = round((food_log.calories or 0) * ratio, 1)
            food_log.protein_g = round((food_log.protein_g or 0) * ratio, 1)
            food_log.fat_g     = round((food_log.fat_g or 0) * ratio, 1)
            food_log.carbs_g   = round((food_log.carbs_g or 0) * ratio, 1)

        food_log.weight_g = new_weight
        food_log.weight_confirmed = True
        await session.commit()
        await session.refresh(food_log)

        await state.clear()
        await message.answer(
            f"✅ <b>Вес обновлён: {new_weight:.0f}г</b>\n\n"
            f"📊 Пересчитанные КБЖУ:\n"
            f"  ├ 🔥 Калории: <b>{food_log.calories:.0f} ккал</b>\n"
            f"  ├ 🥩 Белки: <b>{food_log.protein_g:.1f}г</b>\n"
            f"  ├ 🧈 Жиры: <b>{food_log.fat_g:.1f}г</b>\n"
            f"  └ 🍞 Углеводы: <b>{food_log.carbs_g:.1f}г</b>",
            parse_mode="HTML",
            reply_markup=main_menu_kb(),
        )

    except Exception as e:
        logger.error(f"Weight correction error: {e}", exc_info=True)
        await state.clear()
        await message.answer("😕 Ошибка при обновлении. Попробуй ещё раз.")


# ─── Дневник за сегодня ──────────────────────────────────────────────────────

@router.callback_query(F.data == "food:today")
async def cb_food_today(call: CallbackQuery, db_user, session: AsyncSession):
    stats = await user_service.get_today_nutrition(
        session, db_user.id, local_today(db_user)
    )
    tdee = db_user.tdee_kcal or 2000

    cal = stats["calories"]
    remaining = max(0, tdee - cal)
    pct = min(100, int(cal / tdee * 100)) if tdee else 0

    bar_filled = "█" * (pct // 10)
    bar_empty  = "░" * (10 - len(bar_filled))

    await call.message.edit_text(
        f"📊 <b>Питание сегодня</b>\n\n"
        f"<b>Калории:</b> {cal:.0f} / {tdee:.0f} ккал\n"
        f"[{bar_filled}{bar_empty}] {pct}%\n\n"
        f"├ 🥩 Белки: <b>{stats['protein']:.1f} г</b>\n"
        f"├ 🧈 Жиры: <b>{stats['fat']:.1f} г</b>\n"
        f"└ 🍞 Углеводы: <b>{stats['carbs']:.1f} г</b>\n\n"
        f"{'✅ Норма достигнута!' if remaining == 0 else f'Осталось: <b>{remaining:.0f} ккал</b>'}",
        parse_mode="HTML",
    )
    await call.answer()
