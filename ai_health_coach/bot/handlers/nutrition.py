"""
Модуль питания.

Флоу фото:
1. Пользователь присылает фото → AI распознаёт состав и просит вес.
2. Пользователь вводит вес (граммы) → AI считает КБЖУ, бот сохраняет в БД.

Флоу текста:
1. Пользователь пишет что съел → AI парсит, выдаёт КБЖУ → сохраняем.

ФИКСЫ (v2):
- Умный парсер веса: понимает "итого 240 г", "240", "240г", развёрнутый список с итогом
- AI-ответ после фото показывается сразу (без подмены шаблоном)
- Убрано двойное сообщение с запросом веса
"""

import logging
import io
import re
from datetime import date

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, PhotoSize
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.ai_service import ai_service
from bot.services.user_service import user_service
from bot.services.nutrition_parser import save_nutrition_to_log
from bot.utils.timezone import local_today
from bot.models import FoodLog
from bot.keyboards.main import nutrition_menu_kb, cancel_kb, main_menu_kb

logger = logging.getLogger(__name__)
router = Router()


class NutritionFSM(StatesGroup):
    waiting_photo_weight   = State()   # ждём вес после фото
    waiting_food_text      = State()   # ждём текстовый ввод еды


# ─── Утилита: извлечение веса из текста ──────────────────────────────────────

def _extract_weight_from_text(text: str) -> float | None:
    """
    Умный парсер веса порции. Обрабатывает:
    - Голое число: "240", "350.5"
    - С единицами: "240г", "240 гр", "240 грамм"
    - "Итого 240 г", "Всего 350", "= 240"
    - Развёрнутый список с "Итого NNN гр" в конце
    - Число с запятой: "350,5"

    Возвращает float или None если не удалось распознать.
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # 1. Паттерн "итого/всего/общий вес/= ~NNN"
    match = re.search(
        r'(?:итого|всего|общий\s*вес|суммарн\w*\s*вес|=)\s*~?\s*(\d+[.,]?\d*)',
        text,
        re.IGNORECASE,
    )
    if match:
        return _safe_float(match.group(1))

    # 2. Если текст — просто число (возможно с "г"/"гр"/"грамм")
    clean = re.sub(r'\s*(г|гр|грамм|грамов|gram[s]?)\s*$', '', text, flags=re.IGNORECASE).strip()
    clean = clean.replace(",", ".").strip()
    try:
        val = float(clean)
        return val
    except ValueError:
        pass

    # 3. Если текст длинный (список продуктов) — ищем последнее число
    #    Приоритет: числа рядом со словами "итого"/"всего"
    numbers = re.findall(r'(\d+[.,]?\d*)', text)
    if numbers:
        # Берём последнее число — обычно это итог
        return _safe_float(numbers[-1])

    return None


def _safe_float(s: str) -> float | None:
    try:
        return float(s.replace(",", "."))
    except (ValueError, TypeError):
        return None


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
        "Я сам разберусь с составом и размером порции 👇",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await call.answer()


# ─── Обработка фото ──────────────────────────────────────────────────────────

@router.message(F.photo)
async def handle_food_photo(
    message: Message,
    bot: Bot,
    db_user,
    session: AsyncSession,
    state: FSMContext,
):
    """Шаг 1: принимаем фото, отдаём в AI для распознавания состава."""

    if not db_user.onboarding_done:
        await message.answer("Сначала давай заполним анкету! Нажми /start")
        return

    # Берём фото наилучшего качества
    photo: PhotoSize = message.photo[-1]

    thinking_msg = await message.answer("📸 Анализирую фото...")

    try:
        # Скачиваем фото
        file = await bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        photo_bytes = buf.getvalue()

        profile = user_service.to_profile_dict(db_user)
        response = await ai_service.analyze_food_photo(
            user_id=message.from_user.id,
            photo_bytes=photo_bytes,
            user_profile=profile,
        )

        # Сохраняем file_id для записи в БД после получения веса
        await state.update_data(
            photo_file_id=photo.file_id,
            step="waiting_weight",
        )
        await state.set_state(NutritionFSM.waiting_photo_weight)

        # ⚠️ ВАЖНО: показываем именно ответ AI (распознанные продукты + вопрос про вес).
        # НЕ подменяем на шаблонный текст "Введи общий вес порции..."!
        await thinking_msg.edit_text(response, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Photo analysis error: {e}")
        await thinking_msg.edit_text(
            "😕 Не смог обработать фото. Попробуй снова или опиши блюдо текстом."
        )


# ─── Шаг 2: пользователь ввёл вес порции → считаем КБЖУ ─────────────────────

@router.message(NutritionFSM.waiting_photo_weight)
async def handle_photo_weight(
    message: Message,
    db_user,
    session: AsyncSession,
    state: FSMContext,
):
    """
    Шаг 2: пользователь ввёл вес порции → считаем КБЖУ.

    ФИКС: Умный парсер — понимает:
    - "240"
    - "240г"
    - Развёрнутый список типа:
        "1. Куриная котлета - 100гр
         2. Морская капуста - 80гр
         Итого 240 гр"
    """

    weight_g = _extract_weight_from_text(message.text)

    # Валидация диапазона
    if weight_g is None or not (10 <= weight_g <= 5000):
        await message.answer(
            "⚖️ Не смог распознать вес порции.\n"
            "Введи одно число в граммах, например: <code>240</code>",
            parse_mode="HTML",
        )
        return

    thinking_msg = await message.answer("⚙️ Считаю КБЖУ...")

    try:
        profile = user_service.to_profile_dict(db_user)
        response = await ai_service.calculate_nutrition_with_weight(
            user_id=message.from_user.id,
            weight_g=weight_g,
            user_profile=profile,
        )

        # Достаём file_id из состояния
        fsm_data = await state.get_data()
        photo_file_id = fsm_data.get("photo_file_id")

        # Сохраняем лог с распарсенным КБЖУ
        food_log = FoodLog(
            user_id=db_user.id,
            raw_input=f"[фото] вес: {weight_g:.0f}г",
            is_photo=True,
            photo_file_id=photo_file_id,
            weight_g=weight_g,
            weight_confirmed=True,
            meal_date=local_today(db_user),
        )
        await save_nutrition_to_log(session, food_log, response)

        await state.clear()
        await thinking_msg.edit_text(
            response + "\n\n✅ <i>Записал в дневник!</i>",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Nutrition calc error: {e}")
        await thinking_msg.edit_text("😕 Что-то пошло не так. Попробуй ещё раз.")
        await state.clear()


# ─── Текстовый ввод еды ──────────────────────────────────────────────────────

@router.message(NutritionFSM.waiting_food_text)
async def handle_food_text(
    message: Message,
    db_user,
    session: AsyncSession,
    state: FSMContext,
):
    thinking_msg = await message.answer("🧠 Анализирую...")

    try:
        profile = user_service.to_profile_dict(db_user)
        # Добавляем инструкцию к запросу
        user_msg = (
            f"Я съел: {message.text}\n\n"
            f"Определи КБЖУ и выведи в таблице. "
            f"Если что-то неоднозначно по весу — уточни или укажи диапазон."
        )
        response = await ai_service.chat(
            user_id=message.from_user.id,
            user_message=user_msg,
            user_profile=profile,
        )

        # Сохраняем факт записи с распарсенным КБЖУ
        food_log = FoodLog(
            user_id=db_user.id,
            raw_input=message.text,
            is_photo=False,
            meal_date=local_today(db_user),
        )
        await save_nutrition_to_log(session, food_log, response)

        await state.clear()
        await thinking_msg.edit_text(
            response + "\n\n✅ <i>Записал в дневник!</i>",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Food text error: {e}")
        await thinking_msg.edit_text("😕 Ошибка. Попробуй снова.")
        await state.clear()


# ─── Дневник за сегодня ──────────────────────────────────────────────────────

@router.callback_query(F.data == "food:today")
async def cb_food_today(call: CallbackQuery, db_user, session: AsyncSession):
    stats = await user_service.get_today_nutrition(session, db_user.id, local_today(db_user))
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
