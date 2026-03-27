"""
Тесты для умного парсера веса из nutrition.py

Запуск:
    pytest test_weight_parser.py -v
"""

import pytest
import re


# ─── Копия парсера (для автономного тестирования) ────────────────────────────

def _safe_float(s: str) -> float | None:
    try:
        return float(s.replace(",", "."))
    except (ValueError, TypeError):
        return None


def _extract_weight_from_text(text: str) -> float | None:
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

    # 2. Просто число (возможно с "г"/"гр"/"грамм")
    clean = re.sub(r'\s*(г|гр|грамм|грамов|gram[s]?)\s*$', '', text, flags=re.IGNORECASE).strip()
    clean = clean.replace(",", ".").strip()
    try:
        val = float(clean)
        return val
    except ValueError:
        pass

    # 3. Последнее число в тексте
    numbers = re.findall(r'(\d+[.,]?\d*)', text)
    if numbers:
        return _safe_float(numbers[-1])

    return None


# ─── Тесты ───────────────────────────────────────────────────────────────────

class TestExtractWeight:
    """Тестируем все реальные форматы ввода от пользователей."""

    # --- Простые числа ---

    def test_bare_number(self):
        assert _extract_weight_from_text("240") == 240.0

    def test_bare_number_float(self):
        assert _extract_weight_from_text("350.5") == 350.5

    def test_comma_decimal(self):
        assert _extract_weight_from_text("350,5") == 350.5

    # --- С единицами ---

    def test_with_g(self):
        assert _extract_weight_from_text("240г") == 240.0

    def test_with_g_space(self):
        assert _extract_weight_from_text("240 г") == 240.0

    def test_with_gr(self):
        assert _extract_weight_from_text("240гр") == 240.0

    def test_with_gramm(self):
        assert _extract_weight_from_text("240 грамм") == 240.0

    def test_with_grams_en(self):
        assert _extract_weight_from_text("240 grams") == 240.0

    # --- Итого / Всего ---

    def test_itogo(self):
        assert _extract_weight_from_text("Итого 240 гр") == 240.0

    def test_vsego(self):
        assert _extract_weight_from_text("Всего 350") == 350.0

    def test_equals_sign(self):
        assert _extract_weight_from_text("= 240") == 240.0

    def test_obshiy_ves(self):
        assert _extract_weight_from_text("Общий вес 240") == 240.0

    def test_itogo_tilde(self):
        assert _extract_weight_from_text("Итого ~240 г") == 240.0

    # --- Развёрнутый список (реальный кейс с бага) ---

    def test_full_list_with_itogo(self):
        text = """1. Куриная котлета с кетчупом. - 100 гр
2. Морская капуста -80 гр
3. Соевые спаржи - 60 гр

Итого 240 гр"""
        assert _extract_weight_from_text(text) == 240.0

    def test_full_list_without_itogo(self):
        """Если нет 'итого' — берём последнее число (240)."""
        text = """Котлета 100гр
Капуста 80гр
Спаржа 60гр
240"""
        assert _extract_weight_from_text(text) == 240.0

    def test_full_list_with_vsego(self):
        text = "Рис 150г + курица 150г + кофе 50г = всего 350"
        assert _extract_weight_from_text(text) == 350.0

    # --- Пограничные случаи ---

    def test_none_input(self):
        assert _extract_weight_from_text(None) is None

    def test_empty_string(self):
        assert _extract_weight_from_text("") is None

    def test_whitespace_only(self):
        assert _extract_weight_from_text("   ") is None

    def test_no_numbers(self):
        assert _extract_weight_from_text("нет данных") is None

    def test_min_boundary(self):
        """10г — минимум, парсер вернёт, валидация в хэндлере отсечёт."""
        assert _extract_weight_from_text("10") == 10.0

    def test_large_number(self):
        assert _extract_weight_from_text("2500") == 2500.0

    # --- Формат с "примерно" ---

    def test_primerno(self):
        text = "Примерно 300 грамм"
        assert _extract_weight_from_text(text) == 300.0

    def test_summarny_ves(self):
        text = "Суммарный вес 450г"
        assert _extract_weight_from_text(text) == 450.0
