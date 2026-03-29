"""
Тесты для локальной базы продуктов (fallback).
pytest test_food_database.py -v
"""

import pytest
import sys
import os

os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

from bot.services.food_database import FoodDatabase


@pytest.fixture
def db():
    return FoodDatabase()


class TestLookup:

    def test_exact_match(self, db):
        result = db.lookup("яйцо")
        assert result is not None
        assert result["cal_100"] == 155

    def test_alias_match(self, db):
        result = db.lookup("яичница")
        assert result is not None
        assert result["name"] == "яйцо"

    def test_case_insensitive(self, db):
        result = db.lookup("Куриная Грудка")
        assert result is not None
        assert result["name"] == "куриная грудка"

    def test_substring_match(self, db):
        result = db.lookup("грудка")
        assert result is not None

    def test_not_found(self, db):
        result = db.lookup("xyznonexistent")
        assert result is None

    def test_морская_капуста(self, db):
        result = db.lookup("морская капуста")
        assert result is not None
        assert result["name"] == "морская капуста"


class TestEstimateFromText:

    def test_simple_item(self, db):
        result = db.estimate_from_text("яйцо")
        assert result is not None
        assert len(result["items"]) == 1
        assert result["total"]["calories"] > 0

    def test_two_items_with_and(self, db):
        result = db.estimate_from_text("два яйца и тост")
        assert result is not None
        assert len(result["items"]) == 2
        # 2 яйца = 2×60г по 155ккал/100г = 186 ккал
        # тост = 30г по 261ккал/100г = 78 ккал
        assert result["total"]["calories"] > 200

    def test_explicit_weight(self, db):
        result = db.estimate_from_text("гречка 200г")
        assert result is not None
        assert result["items"][0]["weight_g"] == 200

    def test_comma_separated(self, db):
        result = db.estimate_from_text(
            "куриная котлета 100гр, морская капуста 80гр, соевые спаржи 60гр"
        )
        assert result is not None
        assert len(result["items"]) == 3
        assert result["total"]["weight_g"] == 240

    def test_numeric_quantity(self, db):
        result = db.estimate_from_text("3 яйца")
        assert result is not None
        assert result["items"][0]["weight_g"] == 180  # 3 × 60

    def test_text_quantity(self, db):
        result = db.estimate_from_text("два яйца")
        assert result is not None
        assert result["items"][0]["weight_g"] == 120  # 2 × 60

    def test_unknown_food_returns_none(self, db):
        result = db.estimate_from_text("космическая пыль")
        assert result is None

    def test_mixed_known_unknown(self, db):
        """Если часть продуктов неизвестна — вернуть то что распознали."""
        result = db.estimate_from_text("яйцо и космическая пыль")
        assert result is not None
        assert len(result["items"]) == 1

    def test_result_has_correct_structure(self, db):
        result = db.estimate_from_text("рис 200г и курица 150г")
        assert result is not None
        assert "items" in result
        assert "total" in result
        assert "comment" in result
        assert "confidence" in result
        assert result["confidence"] == "low"

        for item in result["items"]:
            assert "name" in item
            assert "weight_g" in item
            assert "calories" in item
            assert "protein" in item
            assert "fat" in item
            assert "carbs" in item

    def test_real_user_input_kotleta(self, db):
        """Реальный кейс из бага."""
        result = db.estimate_from_text(
            "Куриная котлета с кетчупом 100гр, морская капуста 80гр, соевые спаржи 60гр"
        )
        assert result is not None
        assert len(result["items"]) >= 2  # кетчуп может не матчиться отдельно

    def test_default_weight_used(self, db):
        """Без указания веса — используется дефолтный."""
        result = db.estimate_from_text("банан")
        assert result is not None
        assert result["items"][0]["weight_g"] == 120  # default для банана

    def test_half_quantity(self, db):
        result = db.estimate_from_text("пол авокадо")
        assert result is not None
        assert result["items"][0]["weight_g"] == 75  # 0.5 × 150

    def test_plus_separator(self, db):
        result = db.estimate_from_text("рис 150г + курица 100г")
        assert result is not None
        assert len(result["items"]) == 2

    def test_empty_input(self, db):
        assert db.estimate_from_text("") is None
        assert db.estimate_from_text("   ") is None

    def test_calories_math_correct(self, db):
        """Проверяем правильность расчёта КБЖУ."""
        result = db.estimate_from_text("яйцо")
        item = result["items"][0]
        # 60г яйца: 155 * 60/100 = 93 ккал
        assert item["calories"] == 93
        # белки: 12.6 * 60/100 = 7.56 → 7.6
        assert item["protein"] == pytest.approx(7.6, abs=0.1)
