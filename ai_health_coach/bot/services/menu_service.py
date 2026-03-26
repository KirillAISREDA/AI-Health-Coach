"""
MenuService — предлагает что съесть, исходя из:
- Остатка калорий до нормы
- Остатка белка
- Времени суток (завтрак / обед / ужин / перекус)
- Аллергий пользователя
- Цели (похудение / набор)

Работает через GPT когда нужны сложные комбинации,
и через локальные шаблоны для быстрых ответов.
"""

import logging
from datetime import datetime

import pytz

from bot.utils.timezone import get_user_timezone

logger = logging.getLogger(__name__)


# ── Локальные шаблоны ──────────────────────────────────────────────────────

MEAL_TEMPLATES = {
    "breakfast": {
        "high_protein": [
            ("Омлет из 3 яиц", 220, 18, 16, 2),
            ("Творог 5% 200г + мёд", 210, 22, 6, 20),
            ("Греческий йогурт 200г + ягоды", 180, 16, 4, 20),
            ("Яичница + тост цельнозерновой", 260, 16, 14, 18),
        ],
        "balanced": [
            ("Овсянка 60г + банан", 280, 9, 5, 52),
            ("Гречка 80г + яйцо варёное", 290, 16, 7, 46),
        ],
    },
    "lunch": {
        "high_protein": [
            ("Куриная грудка 200г + рис 100г", 390, 48, 4, 46),
            ("Тунец 150г + гречка 100г", 360, 44, 3, 44),
            ("Говядина 180г + картошка 150г", 420, 40, 14, 30),
        ],
        "balanced": [
            ("Суп куриный + хлеб", 280, 22, 6, 35),
            ("Паста с курицей 300г", 480, 32, 10, 62),
        ],
    },
    "dinner": {
        "high_protein": [
            ("Лосось 180г + овощи на пару", 350, 38, 18, 8),
            ("Творог 5% 250г", 195, 27, 7, 8),
            ("Куриная грудка 200г + салат", 280, 48, 4, 10),
        ],
        "light": [
            ("Омлет 2 яйца + помидор", 160, 12, 11, 4),
            ("Кефир 250мл + хлебцы", 140, 8, 4, 18),
        ],
    },
    "snack": {
        "high_protein": [
            ("Творог 100г", 100, 14, 4, 4),
            ("Яйцо варёное", 80, 7, 6, 0),
            ("Протеиновый батончик ~40г", 160, 15, 5, 14),
        ],
        "light": [
            ("Яблоко + горсть орехов", 170, 4, 10, 16),
            ("Кефир 200мл", 112, 6, 4, 12),
            ("Банан", 90, 1, 0, 23),
        ],
    },
}


def _get_meal_time(user) -> str:
    """Определяет приём пищи по текущему времени."""
    tz = get_user_timezone(user)
    hour = datetime.now(tz).hour
    if 6 <= hour < 11:
        return "breakfast"
    elif 11 <= hour < 16:
        return "lunch"
    elif 16 <= hour < 20:
        return "dinner"
    else:
        return "snack"


def _normalize_allergen(allergen: str) -> list[str]:
    """
    Возвращает список словоформ аллергена для поиска в названии блюда.
    Покрывает наиболее частые русские и английские слова.
    """
    a = allergen.strip().lower()
    # Общие продукты — явный маппинг словоформ
    FORMS: dict[str, list[str]] = {
        "яйца":   ["яиц", "яйц", "яйца", "яйцо"],
        "яйцо":   ["яиц", "яйц", "яйца", "яйцо"],
        "молоко":  ["молок", "молоч"],
        "творог":  ["творог", "твороч"],
        "орехи":   ["орех"],
        "арахис":  ["арахис"],
        "глютен":  ["глютен", "пшениц", "ржан", "ячмен"],
        "рыба":    ["рыб", "лосос", "тунец", "сёмг", "сёмг"],
        "морепродукты": ["креветк", "кальмар", "осьминог", "краб"],
        "лактоза": ["молок", "молоч", "творог", "кефир", "сыр", "йогурт"],
    }
    if a in FORMS:
        return FORMS[a]
    # Для остальных — берём первые 4 символа как корень
    return [a[:4]] if len(a) >= 4 else [a]


def _filter_by_allergies(
    meals: list[tuple],
    allergies: str | None,
) -> list[tuple]:
    """Убирает блюда содержащие аллергены."""
    if not allergies:
        return meals

    # Собираем все формы всех аллергенов
    all_roots: list[str] = []
    for allergen in allergies.split(","):
        all_roots.extend(_normalize_allergen(allergen.strip()))

    filtered = []
    for meal in meals:
        name = meal[0].lower()
        if not any(root in name for root in all_roots):
            filtered.append(meal)
    return filtered or meals  # если всё отфильтровано — вернём всё


class MenuService:

    def suggest_quick(
        self,
        user,
        remaining_kcal: float,
        remaining_protein_g: float,
    ) -> str:
        """
        Быстрое предложение блюда без GPT.
        Используется когда нужен мгновенный ответ.
        """
        meal_time = _get_meal_time(user)
        category  = "high_protein" if remaining_protein_g > 20 else "balanced"

        # Для вечера при снижении веса — лёгкое
        if meal_time == "dinner" and (user.goal or "") == "lose_weight":
            category = "light"
        if meal_time == "snack" and remaining_kcal < 200:
            category = "light"

        options = MEAL_TEMPLATES.get(meal_time, {})
        meals   = options.get(category) or options.get("high_protein", [])
        meals   = _filter_by_allergies(meals, getattr(user, "allergies", None))

        # Выбираем блюдо наиболее близкое к нужным калориям
        target_kcal = min(remaining_kcal, 600)
        best = min(meals, key=lambda m: abs(m[1] - target_kcal))

        name, kcal, prot, fat, carb = best
        meal_labels = {
            "breakfast": "завтрак", "lunch": "обед",
            "dinner": "ужин", "snack": "перекус",
        }

        return (
            f"🍽️ <b>Предложение на {meal_labels[meal_time]}:</b>\n\n"
            f"<b>{name}</b>\n"
            f"├ Ккал: {kcal}\n"
            f"├ Белки: {prot}г\n"
            f"├ Жиры: {fat}г\n"
            f"└ Углеводы: {carb}г\n\n"
            f"После этого останется: <b>{max(0, remaining_kcal - kcal):.0f} ккал</b>"
        )

    async def suggest_with_ai(
        self,
        user,
        remaining_kcal: float,
        remaining_protein_g: float,
        available_products: str = "",
    ) -> str:
        """
        Развёрнутое меню через GPT — для запроса «что приготовить».
        Поддерживает указание доступных продуктов.
        """
        from bot.services.ai_service import ai_service

        meal_time   = _get_meal_time(user)
        allergies   = getattr(user, "allergies", None) or "нет"
        goal_labels = {
            "lose_weight":   "снижение веса",
            "gain_muscle":   "набор мышечной массы",
            "maintain":      "поддержание веса",
            "recomposition": "рекомпозиция",
        }
        goal = goal_labels.get(getattr(user, "goal", "maintain"), "поддержание")

        meal_labels = {
            "breakfast": "завтрак", "lunch": "обед",
            "dinner": "ужин", "snack": "перекус",
        }

        products_clause = (
            f"\nДоступные продукты: {available_products}" if available_products
            else ""
        )

        prompt = (
            f"Предложи конкретное блюдо или несколько вариантов на {meal_labels[meal_time]}.\n\n"
            f"Параметры:\n"
            f"- Оставшиеся калории до нормы: {remaining_kcal:.0f} ккал\n"
            f"- Нужно добрать белка: {remaining_protein_g:.0f}г\n"
            f"- Цель: {goal}\n"
            f"- Аллергии/ограничения: {allergies}"
            f"{products_clause}\n\n"
            f"Формат: название блюда, КБЖУ таблицей, краткий рецепт (3-5 шагов). "
            f"Максимум 2 варианта. Реалистично и вкусно!"
        )

        profile = {
            "goal": getattr(user, "goal", None),
            "allergies": allergies,
            "timezone": getattr(user, "timezone", "UTC"),
        }

        return await ai_service.chat(
            user_id=user.id,
            user_message=prompt,
            user_profile=profile,
        )

    def format_daily_plan(
        self,
        tdee: float,
        protein_goal: float,
        eaten_kcal: float,
        eaten_protein: float,
    ) -> str:
        """Краткая сводка: сколько осталось на день."""
        remaining_kcal    = max(0, tdee - eaten_kcal)
        remaining_protein = max(0, protein_goal - eaten_protein)
        pct_done = int(eaten_kcal / tdee * 100) if tdee else 0

        bar_filled = "█" * (pct_done // 10)
        bar_empty  = "░" * (10 - len(bar_filled))

        return (
            f"📊 <b>Сегодня:</b>\n"
            f"[{bar_filled}{bar_empty}] {pct_done}%\n"
            f"Съедено: {eaten_kcal:.0f} / {tdee:.0f} ккал\n\n"
            f"Осталось: <b>{remaining_kcal:.0f} ккал</b>\n"
            f"Белка: <b>{remaining_protein:.0f}г</b> до нормы"
        )


menu_service = MenuService()
