"""
Тесты флоу записи еды — три проблемы из продакшна:
1. Парсер с **bold** ячейками (calories=NULL → статистика = 0)
2. Двойной счёт строки «Итого» в таблице
3. Статистика не суммирует несколько приёмов пищи
"""

import pytest
import os
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

from datetime import date
from bot.services.nutrition_parser import parse_nutrition_from_text


# ── Реальные ответы GPT из продакшна ────────────────────────────────────────

PHOTO_RESPONSE_WITH_BOLD = """
Спасибо за информацию! Рассчитаем КБЖУ для каждого элемента блюда весом 280 г.

| Блюдо | Вес (г) | Ккал | Белки (г) | Жиры (г) | Углеводы (г) |
|---|---|---|---|---|---|
| **Тушёные овощи** | **120** | **100** | **3** | **5** | **12** |
| **Рис и салат** | **120** | **130** | **3** | **2** | **26** |
| **Хлеб** | **40** | **90** | **2** | **1** | **18** |
| **Итого** | **280** | **320** | **8** | **8** | **56** |

Комментарий: Отличный сбалансированный обед!
"""

PHOTO_RESPONSE_NO_BOLD = """
| Тушёные овощи | 120 | 100 | 3 | 5 | 12 |
| Рис и салат | 120 | 130 | 3 | 2 | 26 |
| Хлеб | 40 | 90 | 2 | 1 | 18 |
| Итого | 280 | 320 | 8 | 8 | 56 |
"""

TEXT_RESPONSE_SWEETS = """
Добавим информацию о конфетах, булочке и чае:

| Блюдо | Вес (г) | Ккал | Белки (г) | Жиры (г) | Углеводы (г) |
|---|---|---|---|---|---|
| Шоколадные конфеты | 25 | 130 | 2 | 8 | 15 |
| Булочка белая | 80 | 220 | 6 | 4 | 40 |
| Чай | 220 мл | 0 | 0 | 0 | 0 |

✅ Записал в дневник!
"""

TEXT_RESPONSE_BREAKFAST = """
| Творог с моцареллой | 60 | 72 | 11 | 2 | 3 |
| Рис варёный | 80 | 104 | 2 | 0 | 23 |
| Капучино 300 мл | 40 | 4 | 0 | 0 | 0 |
| Итого | 180 | 180 | 13 | 2 | 26 |
"""


class TestParserBoldFix:
    """Баг: GPT оборачивает числа в **bold** → calories=NULL"""

    def test_bold_cells_parsed(self):
        r = parse_nutrition_from_text(PHOTO_RESPONSE_WITH_BOLD)
        assert r != {}, "Bold-ячейки должны парситься"
        assert r["calories"] > 0

    def test_bold_same_as_plain(self):
        r_bold  = parse_nutrition_from_text(PHOTO_RESPONSE_WITH_BOLD)
        r_plain = parse_nutrition_from_text(PHOTO_RESPONSE_NO_BOLD)
        assert r_bold["calories"] == r_plain["calories"]
        assert r_bold["protein_g"] == r_plain["protein_g"]

    def test_bold_calories_is_320_not_640(self):
        """Итого=320, значит суммирование строк без двойного счёта даёт 320"""
        r = parse_nutrition_from_text(PHOTO_RESPONSE_WITH_BOLD)
        assert r["calories"] == pytest.approx(320, abs=2)

    def test_text_response_parsed(self):
        r = parse_nutrition_from_text(TEXT_RESPONSE_SWEETS)
        assert r["calories"] == pytest.approx(350, abs=2)  # 130+220+0
        assert r["carbs_g"] == pytest.approx(55, abs=2)    # 15+40+0


class TestSummaryRowExclusion:
    """Баг: строка «Итого» суммировалась дважды (320+320=640 вместо 320)"""

    def test_no_double_count_with_itogo(self):
        r = parse_nutrition_from_text(PHOTO_RESPONSE_NO_BOLD)
        assert r["calories"] == pytest.approx(320, abs=2), \
            f"Должно быть 320, получилось {r['calories']} (возможно двойной счёт)"

    def test_itogo_alone_returns_empty(self):
        itogo_only = "| Итого | 280 | 320 | 8 | 8 | 56 |"
        r = parse_nutrition_from_text(itogo_only)
        assert r == {}, "Одиночная строка 'Итого' без данных должна вернуть {}"

    def test_total_row_skipped(self):
        text = """
        | Курица | 150 | 165 | 31 | 4 | 0 |
        | Гречка | 150 | 165 | 6 | 2 | 33 |
        | Итого  | 300 | 330 | 37 | 6 | 33 |
        """
        r = parse_nutrition_from_text(text)
        # Должно быть 165+165=330, а не 330+330=660
        assert r["calories"] == pytest.approx(330, abs=2), \
            f"Ожидали 330, получили {r['calories']}"

    def test_total_english_skipped(self):
        text = """
        | Rice | 100 | 130 | 3 | 0 | 28 |
        | Total | 100 | 130 | 3 | 0 | 28 |
        """
        r = parse_nutrition_from_text(text)
        assert r["calories"] == pytest.approx(130, abs=2)

    def test_vsego_skipped(self):
        text = """
        | Яйцо | 60 | 80 | 7 | 6 | 0 |
        | Всего | 60 | 80 | 7 | 6 | 0 |
        """
        r = parse_nutrition_from_text(text)
        assert r["calories"] == pytest.approx(80, abs=2)


class TestMultipleMealAccumulation:
    """Баг: статистика показывала только 1 из 4 приёмов"""

    def test_four_meals_total(self):
        """Проверяем что 4 разных ответа GPT дадут правильную сумму"""
        meals = [
            parse_nutrition_from_text(TEXT_RESPONSE_BREAKFAST),   # 180 ккал
            parse_nutrition_from_text(PHOTO_RESPONSE_NO_BOLD),    # 320 ккал
            parse_nutrition_from_text(TEXT_RESPONSE_SWEETS),      # 350 ккал
        ]

        assert all(m for m in meals), "Все приёмы должны распарситься"

        total = sum(m["calories"] for m in meals)
        # 180 + 320 + 350 = 850
        assert total == pytest.approx(850, abs=5), \
            f"Сумма 3 приёмов должна быть ~850, получилось {total}"

    def test_each_meal_has_nonzero_calories(self):
        for name, resp in [
            ("завтрак", TEXT_RESPONSE_BREAKFAST),
            ("обед", PHOTO_RESPONSE_NO_BOLD),
            ("перекус", TEXT_RESPONSE_SWEETS),
        ]:
            r = parse_nutrition_from_text(resp)
            assert r.get("calories", 0) > 0, \
                f"Приём «{name}» должен иметь calories > 0, получили {r}"


class TestParserEdgeCases:
    """Граничные случаи из реальных ответов GPT"""

    def test_chay_220ml_zero_calories(self):
        """Чай 220 мл = 0 ккал — не должен мешать парсингу других строк"""
        r = parse_nutrition_from_text(TEXT_RESPONSE_SWEETS)
        assert r["calories"] == pytest.approx(350, abs=5)

    def test_disclaimer_at_end_not_parsed(self):
        text = """
        | Булочка | 80 | 260 | 5 | 10 | 38 |
        ⚠️ Информация носит рекомендательный характер. Проконсультируйся с врачом.
        """
        r = parse_nutrition_from_text(text)
        assert r["calories"] == pytest.approx(260, abs=2)

    def test_checkmark_emoji_before_text_ignored(self):
        text = "✅ Записал в дневник!\n| Курица | 100 | 165 | 31 | 4 | 0 |"
        r = parse_nutrition_from_text(text)
        assert r["calories"] == pytest.approx(165, abs=2)
