"""
NutritionParser — извлекает числа КБЖУ из текстового ответа GPT
и обновляет запись FoodLog в базе данных.

GPT возвращает таблицу вида:
| Блюдо       | Вес (г) | Ккал | Белки (г) | Жиры (г) | Углеводы (г) |
| Куриная грудка | 200  | 220  | 46        | 2.4      | 0           |

Парсер ищет итоговую строку (или суммирует все строки) и сохраняет
агрегированные значения.
"""

import re
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import FoodLog

logger = logging.getLogger(__name__)


def _to_float(s: str) -> Optional[float]:
    """'2.4' / '2,4' / '~220' → 2.4 / 220.0"""
    try:
        return float(s.strip().replace(",", ".").replace("~", "").replace("≈", ""))
    except (ValueError, AttributeError):
        return None


def parse_nutrition_from_text(text: str) -> dict:
    """
    Ищет в тексте строки таблицы с КБЖУ.
    Возвращает {'calories': ..., 'protein_g': ..., 'fat_g': ..., 'carbs_g': ...}
    Если не найдено — возвращает пустой dict.
    """
    # Убираем markdown-форматирование (GPT иногда оборачивает числа в **...**)
    clean = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    # Убираем HTML-теги если попались
    clean = re.sub(r'<[^>]+>', '', clean)

    calories = protein = fat = carbs = 0.0
    found = False

    # Ищем строки таблицы markdown: | ... | число[ед] | число | число | число | число |
    # Вес может содержать единицы: "400 г" или "400"
    table_row = re.compile(
        r"\|\s*[^|]+\|\s*[\d.,~≈]+\s*(?:г|g|мл|ml)?\s*\|"  # блюдо | вес[ед] |
        r"\s*([\d.,~≈]+)\s*\|"                                 # ккал
        r"\s*([\d.,~≈]+)\s*\|"                                 # белки
        r"\s*([\d.,~≈]+)\s*\|"                                 # жиры
        r"\s*([\d.,~≈]+)\s*\|"                                 # углеводы
    )

    # Ищем строки таблицы markdown вместе с именем блюда
    table_row_full = re.compile(
        r"\|\s*([^|]+?)\s*\|\s*[\d.,~≈]+\s*(?:г|g|мл|ml)?\s*\|"  # блюдо | вес[ед] |
        r"\s*([\d.,~≈]+)\s*\|"                                       # ккал
        r"\s*([\d.,~≈]+)\s*\|"                                       # белки
        r"\s*([\d.,~≈]+)\s*\|"                                       # жиры
        r"\s*([\d.,~≈]+)\s*\|"                                       # углеводы
    )

    # Также ловим упрощённую таблицу без колонки веса:
    # | Продукт | Ккал | Белки | Жиры | Углеводы |
    table_row_simple = re.compile(
        r"\|\s*([^|]+?)\s*\|"           # блюдо
        r"\s*([\d.,~≈]+)\s*\|"          # ккал
        r"\s*([\d.,~≈]+)\s*\|"          # белки
        r"\s*([\d.,~≈]+)\s*\|"          # жиры
        r"\s*([\d.,~≈]+)\s*\|"          # углеводы
    )

    summary_words = ["итого", "итог", "total", "всего", "среднее", "average", "sum"]
    summary_row = None  # сохраняем итоговую строку на случай если обычных нет

    rows = table_row_full.findall(clean)
    if rows:
        for dish, kcal, prot, f, carb in rows:
            dish_lower = dish.strip().lower()
            is_summary = any(w in dish_lower for w in summary_words)
            if is_summary:
                summary_row = (kcal, prot, f, carb)
                continue
            v_kcal = _to_float(kcal)
            v_prot = _to_float(prot)
            v_fat  = _to_float(f)
            v_carb = _to_float(carb)
            if v_kcal and v_kcal > 5:
                calories += v_kcal
                protein  += v_prot or 0
                fat      += v_fat  or 0
                carbs    += v_carb or 0
                found = True

    # Пробуем упрощённую таблицу (без колонки веса)
    if not found:
        rows_simple = table_row_simple.findall(clean)
        for dish, kcal, prot, f, carb in rows_simple:
            dish_lower = dish.strip().lower()
            # Пропускаем заголовки и разделители
            if any(w in dish_lower for w in ["блюдо", "продукт", "---", "ккал"]):
                continue
            is_summary = any(w in dish_lower for w in summary_words)
            if is_summary:
                summary_row = (kcal, prot, f, carb)
                continue
            v_kcal = _to_float(kcal)
            v_prot = _to_float(prot)
            v_fat  = _to_float(f)
            v_carb = _to_float(carb)
            if v_kcal and v_kcal > 5:
                calories += v_kcal
                protein  += v_prot or 0
                fat      += v_fat  or 0
                carbs    += v_carb or 0
                found = True

    # Если нашли только итоговую строку — используем её
    if not found and summary_row:
        v_kcal = _to_float(summary_row[0])
        if v_kcal and v_kcal > 5:
            calories = v_kcal
            protein  = _to_float(summary_row[1]) or 0
            fat      = _to_float(summary_row[2]) or 0
            carbs    = _to_float(summary_row[3]) or 0
            found = True

    # Если таблицы нет — ищем паттерны типа "Ккал: 350" / "калорий: 350"
    if not found:
        patterns = [
            (r"(?:ккал|калори[ий]|calories)[:\s~≈]+(\d+[\.,]?\d*)",    "calories"),
            (r"(?:белк\w+|protein)[:\s~≈]+(\d+[\.,]?\d*)",              "protein"),
            (r"(?:жир\w*|fat)[:\s~≈]+(\d+[\.,]?\d*)",                  "fat"),
            (r"(?:углевод\w*|carb[s]?)[:\s~≈]+(\d+[\.,]?\d*)",         "carbs"),
        ]
        result = {}
        for pattern, key in patterns:
            m = re.search(pattern, clean, re.IGNORECASE)
            if m:
                result[key] = _to_float(m.group(1)) or 0
                found = True
        if found:
            calories = result.get("calories", 0)
            protein  = result.get("protein", 0)
            fat      = result.get("fat", 0)
            carbs    = result.get("carbs", 0)

    if not found or calories == 0:
        return {}

    return {
        "calories":  round(calories, 1),
        "protein_g": round(protein, 1),
        "fat_g":     round(fat, 1),
        "carbs_g":   round(carbs, 1),
    }


async def save_nutrition_to_log(
    session: AsyncSession,
    food_log: FoodLog,
    ai_response: str,
    description: Optional[str] = None,
) -> FoodLog:
    """
    Парсит КБЖУ из ответа AI и сохраняет в food_log.
    Если парсер не нашёл данные — лог сохраняется без КБЖУ
    (пользователь может скорректировать позже).
    """
    nutrition = parse_nutrition_from_text(ai_response)

    food_log.description = description or ai_response[:500]
    food_log.calories  = nutrition.get("calories")
    food_log.protein_g = nutrition.get("protein_g")
    food_log.fat_g     = nutrition.get("fat_g")
    food_log.carbs_g   = nutrition.get("carbs_g")

    session.add(food_log)
    await session.commit()
    await session.refresh(food_log)

    if nutrition:
        logger.info(
            f"Nutrition saved for user {food_log.user_id}: "
            f"{nutrition['calories']} kcal"
        )
    else:
        logger.warning(
            f"Could not parse nutrition from AI response for user {food_log.user_id}"
        )

    return food_log
