"""
Модуль питания.

Флоу фото:
1. Пользователь присылает фото → AI распознаёт состав и просит вес.
2. Пользователь вводит вес (граммы) → AI считает КБЖУ, бот сохраняет в БД.

Флоу текста:
1. Пользователь пишет что съел → AI парсит, выдаёт КБЖУ → сохраняем.
"""

import logging
import io
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



# ─── Хелпер: красивый результат записи еды ───────────────────────────────────

async def _show_meal_result(
    msg,
    session,
    db_user,
    raw_text: str,
    nutrition: dict,
) -> None:
    """Показывает результат записи: этот приём + итог за день."""
    from bot.utils.timezone import local_today
    from bot.services.user_service import user_service

    if not nutrition:
        await msg.edit_text(
            "✅ Записал в дневник!\n\n"
            "<i>КБЖУ не удалось распознать автоматически.</i>",
            parse_mode="HTML",
        )
        return

    cal   = nutrition["calories"]
    prot  = nutrition["protein_g"]
    fat   = nutrition["fat_g"]
    carbs = nutrition["carbs_g"]
    tdee  = db_user.tdee_kcal or 2000

    def fmt(v): return f"{v:.0f}г" if v > 0 else "—"

    # Загружаем свежую статистику за день (уже включает только что сохранённое)
    today_stats = await user_service.get_today_nutrition(
        session, db_user.id, local_today(db_user)
    )
    day_cal  = today_stats["calories"]
    day_prot = today_stats["protein"]
    day_fat  = today_stats["fat"]
    day_carbs = today_stats["carbs"]
    remaining = max(0, tdee - day_cal)
    pct = min(100, int(day_cal / tdee * 100)) if tdee else 0
    bar_f = "█" * (pct // 10)
    bar_e = "░" * (10 - len(bar_f))

    await msg.edit_text(
        f"✅ <b>Записал в дневник!</b>\n\n"
        f"🍽 <b>Этот приём:</b>\n"
        f"  🔥 {cal:.0f} ккал  "
        f"🥩 {fmt(prot)}  "
        f"🧈 {fmt(fat)}  "
        f"🍞 {fmt(carbs)}\n\n"
        f"📊 <b>Итого за сегодня:</b>\n"
        f"[{bar_f}{bar_e}] {pct}%\n"
        f"{day_cal:.0f} / {tdee:.0f} ккал"
        + (f"  <i>(осталось {remaining:.0f})</i>" if remaining > 0 else " ✅")
        + f"\n🥩 {fmt(day_prot)}  🧈 {fmt(day_fat)}  🍞 {fmt(day_carbs)}\n\n"
        "<i>Данные носят рекомендательный характер. Проконсультируйся с врачом.</i>",
        parse_mode="HTML",
    )


# ─── Меню питания ────────────────────────────────────────────────────────────

@router.message(F.text == "🥗 Питание")
async def nutrition_menu(message: Message):
    await message.answer(
        "🥗 <b>Модуль питания</b>\n\n"
        "📸 <b>Фото блюда</b> — просто прикрепи фото через скрепку 📎 или камеру внизу экрана. "
        "Я автоматически распознаю состав и посчитаю КБЖУ.\n\n"
        "✏️ <b>Текстом</b> — напиши что съел в свободной форме:\n"
        "<i>«гречка с котлетой, 300г» или «съел пиццу маргарита»</i>",
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

    # Если к фото приложен текст — используем как подсказку для AI
    caption = message.caption.strip() if message.caption else None

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
            caption=caption,       # передаём описание пользователя
        )

        # Если в caption уже указан вес/состав — пробуем сразу считать КБЖУ
        # Иначе спрашиваем вес
        await state.update_data(
            photo_file_id=photo.file_id,
            photo_caption=caption or "",
            step="waiting_weight",
        )
        await state.set_state(NutritionFSM.waiting_photo_weight)

        await thinking_msg.edit_text(response, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Photo analysis error: {e}")
        await thinking_msg.edit_text(
            "😕 Не смог обработать фото. Попробуй снова или опиши блюдо текстом."
        )


@router.message(NutritionFSM.waiting_photo_weight)
async def handle_photo_weight(
    message: Message,
    db_user,
    session: AsyncSession,
    state: FSMContext,
):
    """Шаг 2: пользователь ввёл вес порции → считаем КБЖУ."""

    # Валидируем вес
    try:
        weight_g = float(message.text.strip().replace(",", ".").replace("г", "").strip())
        assert 10 <= weight_g <= 3000
    except (ValueError, AssertionError):
        await message.answer(
            "⚖️ Введи <b>общий вес порции</b> в граммах.\n\n"
            "Если на фото несколько блюд — введи суммарный вес всего что ешь, например: <code>350</code>\n"
            "(рис ~150г + курица ~150г + кофе ~50г = ~350г)",
            parse_mode="HTML",
        )
        return

    thinking_msg = await message.answer(f"⚙️ Считаю КБЖУ для {weight_g:.0f}г...")

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
            raw_input=f"[фото] {fsm_data.get('photo_caption', '') or ''} вес: {weight_g}г".strip(),
            is_photo=True,
            photo_file_id=photo_file_id,
            weight_g=weight_g,
            weight_confirmed=True,
            meal_date=local_today(db_user),
        )
        await save_nutrition_to_log(session, food_log, response)

        await state.clear()

        # Строим красивый HTML — без повтора описания блюд
        from bot.services.nutrition_parser import parse_nutrition_from_text
        nutrition = parse_nutrition_from_text(response)

        if nutrition and nutrition.get("calories", 0) > 0:
            cal   = nutrition["calories"]
            prot  = nutrition["protein_g"]
            fat   = nutrition["fat_g"]
            carbs = nutrition["carbs_g"]
            tdee  = db_user.tdee_kcal or 2000

            today_stats = await user_service.get_today_nutrition(session, db_user.id, local_today(db_user))
            day_cal   = today_stats["calories"]
            remaining = max(0, tdee - day_cal)
            pct = min(100, int(day_cal / tdee * 100)) if tdee else 0
            bar_f = "█" * (pct // 10); bar_e = "░" * (10 - len(bar_f))
            def fmt(v): return f"{v:.0f}г" if v > 0 else "—"

            await thinking_msg.edit_text(
                f"✅ <b>Записал в дневник!</b>\n\n"
                f"🥗 <b>Этот приём ({weight_g:.0f}г):</b> {cal:.0f} ккал  "
                f"🥩{fmt(prot)}  🧈{fmt(fat)}  🍞{fmt(carbs)}\n\n"
                f"📊 <b>Итого за сегодня:</b>\n"
                f"[{bar_f}{bar_e}] {pct}%\n"
                f"{day_cal:.0f} / {tdee:.0f} ккал"
                + (f"  <i>(осталось {remaining:.0f})</i>" if remaining > 0 else " ✅") + "\n\n"
                f"<i>Данные носят рекомендательный характер.</i>",
                parse_mode="HTML",
            )
        else:
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

        # Красивый HTML-ответ — без сырой таблицы GPT
        from bot.services.nutrition_parser import parse_nutrition_from_text
        nutrition = parse_nutrition_from_text(response)

        if nutrition and nutrition.get("calories", 0) > 0:
            cal   = nutrition["calories"]
            prot  = nutrition["protein_g"]
            fat   = nutrition["fat_g"]
            carbs = nutrition["carbs_g"]
            tdee  = db_user.tdee_kcal or 2000

            today_stats = await user_service.get_today_nutrition(session, db_user.id, local_today(db_user))
            day_cal   = today_stats["calories"]
            remaining = max(0, tdee - day_cal)
            pct = min(100, int(day_cal / tdee * 100)) if tdee else 0
            bar_f = "█" * (pct // 10); bar_e = "░" * (10 - len(bar_f))
            def fmt(v): return f"{v:.0f}г" if v > 0 else "—"

            await thinking_msg.edit_text(
                f"✅ <b>Записал в дневник!</b>\n\n"
                f"🥗 <b>Этот приём:</b> {cal:.0f} ккал  "
                f"🥩{fmt(prot)}  🧈{fmt(fat)}  🍞{fmt(carbs)}\n\n"
                f"📊 <b>Итого за сегодня:</b>\n"
                f"[{bar_f}{bar_e}] {pct}%\n"
                f"{day_cal:.0f} / {tdee:.0f} ккал"
                + (f"  <i>(осталось {remaining:.0f})</i>" if remaining > 0 else " ✅") + "\n\n"
                f"<i>Данные носят рекомендательный характер.</i>",
                parse_mode="HTML",
            )
        else:
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
