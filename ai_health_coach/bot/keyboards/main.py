from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder


# ─── Reply (главное меню) ────────────────────────────────────────────────────

def main_menu_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="🥗 Питание"),
        KeyboardButton(text="🏋️ Тренировка"),
    )
    builder.row(
        KeyboardButton(text="💧 Вода"),
        KeyboardButton(text="💊 БАДы"),
    )
    builder.row(
        KeyboardButton(text="😴 Сон"),
        KeyboardButton(text="📊 Статистика"),
    )
    builder.row(
        KeyboardButton(text="⚙️ Профиль"),
    )
    return builder.as_markup(resize_keyboard=True)


# ─── Онбординг ───────────────────────────────────────────────────────────────

def gender_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👨 Мужской", callback_data="gender:male"),
        InlineKeyboardButton(text="👩 Женский", callback_data="gender:female"),
    )
    return builder.as_markup()


def goal_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔻 Похудение", callback_data="goal:lose_weight"),
        InlineKeyboardButton(text="💪 Набор массы", callback_data="goal:gain_muscle"),
    )
    builder.row(
        InlineKeyboardButton(text="⚖️ Поддержание", callback_data="goal:maintain"),
        InlineKeyboardButton(text="🔄 Рекомпозиция", callback_data="goal:recomposition"),
    )
    return builder.as_markup()


def activity_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    items = [
        ("🪑 Сидячий образ жизни", "sedentary"),
        ("🚶 1–2 тренировки/нед", "light"),
        ("🏃 3–4 тренировки/нед", "moderate"),
        ("🔥 5+ тренировок/нед", "active"),
        ("🏆 Профессиональный атлет", "very_active"),
    ]
    for label, value in items:
        builder.row(InlineKeyboardButton(text=label, callback_data=f"activity:{value}"))
    return builder.as_markup()


def skip_kb(callback: str = "skip") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Пропустить →", callback_data=callback))
    return builder.as_markup()


# ─── Вода ────────────────────────────────────────────────────────────────────

def water_quick_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💧 +150 мл", callback_data="water:150"),
        InlineKeyboardButton(text="💧 +250 мл", callback_data="water:250"),
        InlineKeyboardButton(text="💧 +500 мл", callback_data="water:500"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Сколько выпил сегодня?", callback_data="water:status"),
    )
    return builder.as_markup()


# ─── БАДы ────────────────────────────────────────────────────────────────────

def supplement_taken_kb(supplement_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Принял", callback_data=f"sup_taken:{supplement_id}"),
        InlineKeyboardButton(text="⏭️ Пропустил", callback_data=f"sup_skip:{supplement_id}"),
    )
    return builder.as_markup()


def supplements_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Добавить БАД", callback_data="sup:add"),
        InlineKeyboardButton(text="📋 Мои БАДы", callback_data="sup:list"),
    )
    builder.row(
        InlineKeyboardButton(text="🔗 Проверить совместимость", callback_data="sup:compat"),
    )
    return builder.as_markup()


# ─── Питание ─────────────────────────────────────────────────────────────────

def nutrition_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✏️ Записать текстом",   callback_data="food:text"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Дневник за сегодня", callback_data="food:today"),
    )
    return builder.as_markup()


# ─── Общие ───────────────────────────────────────────────────────────────────

def cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


def confirm_kb(confirm_data: str, cancel_data: str = "cancel") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да", callback_data=confirm_data),
        InlineKeyboardButton(text="❌ Нет", callback_data=cancel_data),
    )
    return builder.as_markup()
