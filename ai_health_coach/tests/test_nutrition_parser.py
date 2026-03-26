"""
Тесты парсера КБЖУ.

Запуск:
    pytest tests/test_nutrition_parser.py -v
"""

import pytest
from bot.services.nutrition_parser import parse_nutrition_from_text


# --- Фикстуры с реальными ответами GPT -----------------------------------

TABLE_RESPONSE = """
🥗 Вот КБЖУ для твоей порции:

| Блюдо            | Вес (г) | Ккал | Белки (г) | Жиры (г) | Углеводы (г) |
|------------------|---------|------|-----------|----------|--------------|
| Куриная грудка   | 200     | 220  | 46        | 2.4      | 0            |
| Гречка варёная   | 150     | 165  | 5.7       | 1.2      | 33           |
| Огурец           | 80      | 10   | 0.6       | 0.1      | 1.8          |

_⚠️ Информация носит рекомендательный характер. Проконсультируйся с врачом._
"""

SINGLE_ROW_RESPONSE = """
Анализ блюда:

| Блюдо        | Вес (г) | Ккал | Белки (г) | Жиры (г) | Углеводы (г) |
|--------------|---------|------|-----------|----------|--------------|
| Пицца Маргарита | 350  | 820  | 32        | 28       | 102          |

Жирновато, но вписывается в дневную норму при аккуратном ужине 😉
"""

INLINE_RESPONSE = """
Приблизительно:
- Калорий: 450 ккал
- Белков: 38 г
- Жиров: 12 г
- Углеводов: 48 г
"""

INLINE_RUSSIAN_RESPONSE = """
Оценка:
Калории: ~520 ккал
Белки: 42 г
Жиры: 18,5 г
Углеводы: 55 г
"""

COMMA_DECIMAL_RESPONSE = """
| Шаурма с курицей | 400 г | 680 | 34,5 | 28,0 | 62,5 |
"""

APPROXIMATE_RESPONSE = """
| Борщ домашний | 350 | ~280 | ~9 | ~8 | ~35 |
"""

NO_NUTRITION_RESPONSE = """
Отличный выбор! Пришли фото поближе, чтобы я лучше рассмотрел состав блюда.
"""

HEADER_ONLY_RESPONSE = """
| Блюдо | Вес (г) | Ккал | Белки (г) | Жиры (г) | Углеводы (г) |
|-------|---------|------|-----------|----------|--------------|
"""


# --- Тесты ---------------------------------------------------------------

class TestTableParser:

    def test_multi_row_table_sums_correctly(self):
        result = parse_nutrition_from_text(TABLE_RESPONSE)
        assert result != {}
        # 220 + 165 + 10 = 395
        assert result["calories"] == pytest.approx(395.0, abs=1)
        # 46 + 5.7 + 0.6 = 52.3
        assert result["protein_g"] == pytest.approx(52.3, abs=1.0)

    def test_single_row_table(self):
        result = parse_nutrition_from_text(SINGLE_ROW_RESPONSE)
        assert result["calories"] == pytest.approx(820.0, abs=1)
        assert result["protein_g"] == pytest.approx(32.0, abs=0.5)
        assert result["fat_g"] == pytest.approx(28.0, abs=0.5)
        assert result["carbs_g"] == pytest.approx(102.0, abs=0.5)

    def test_comma_decimal_in_table(self):
        result = parse_nutrition_from_text(COMMA_DECIMAL_RESPONSE)
        assert result["calories"] == pytest.approx(680.0, abs=1)
        assert result["protein_g"] == pytest.approx(34.5, abs=0.5)

    def test_approximate_tilde_values(self):
        result = parse_nutrition_from_text(APPROXIMATE_RESPONSE)
        assert result["calories"] == pytest.approx(280.0, abs=1)
        assert result["protein_g"] == pytest.approx(9.0, abs=0.5)

    def test_header_only_returns_empty(self):
        result = parse_nutrition_from_text(HEADER_ONLY_RESPONSE)
        assert result == {}


class TestInlineParser:

    def test_inline_kcal_keyword(self):
        result = parse_nutrition_from_text(INLINE_RESPONSE)
        assert result["calories"] == pytest.approx(450.0, abs=1)
        assert result["protein_g"] == pytest.approx(38.0, abs=0.5)
        assert result["fat_g"] == pytest.approx(12.0, abs=0.5)
        assert result["carbs_g"] == pytest.approx(48.0, abs=0.5)

    def test_inline_russian_with_comma_decimal(self):
        result = parse_nutrition_from_text(INLINE_RUSSIAN_RESPONSE)
        assert result["calories"] == pytest.approx(520.0, abs=1)
        assert result["fat_g"] == pytest.approx(18.5, abs=0.5)

    def test_no_nutrition_data_returns_empty(self):
        result = parse_nutrition_from_text(NO_NUTRITION_RESPONSE)
        assert result == {}

    def test_empty_string_returns_empty(self):
        result = parse_nutrition_from_text("")
        assert result == {}

    def test_random_text_returns_empty(self):
        result = parse_nutrition_from_text("Привет! Как дела? Сегодня хорошая погода.")
        assert result == {}


class TestEdgeCases:

    def test_calories_zero_filtered_out(self):
        # Строка с нулями не должна ломать парсер
        text = "| Вода | 200 | 0 | 0 | 0 | 0 |"
        result = parse_nutrition_from_text(text)
        # calories=0 → пустой результат (нет смысла сохранять)
        assert result == {}

    def test_very_large_values_parsed(self):
        text = "| Торт наполеон | 500 | 1850 | 28 | 96 | 240 |"
        result = parse_nutrition_from_text(text)
        assert result["calories"] == pytest.approx(1850.0, abs=1)

    def test_result_keys_present(self):
        result = parse_nutrition_from_text(SINGLE_ROW_RESPONSE)
        assert set(result.keys()) == {"calories", "protein_g", "fat_g", "carbs_g"}

    def test_values_rounded_to_one_decimal(self):
        result = parse_nutrition_from_text(TABLE_RESPONSE)
        for key, val in result.items():
            # Проверяем что округление до 1 знака
            assert round(val, 1) == val
