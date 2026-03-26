"""
Тесты MenuService.
Запуск: pytest tests/test_menu_service.py -v
"""

import pytest
import os
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

from unittest.mock import MagicMock, patch
from datetime import datetime

import pytz

from bot.services.menu_service import MenuService, _get_meal_time, _filter_by_allergies


def make_user(goal="lose_weight", allergies=None, tz="Europe/Moscow"):
    u = MagicMock()
    u.id = 1
    u.goal = goal
    u.allergies = allergies
    u.timezone = tz
    return u


svc = MenuService()


class TestGetMealTime:

    def _mock_hour(self, hour: int, tz_name="Europe/Moscow"):
        """Мокаем datetime.now() для возврата нужного часа."""
        tz = pytz.timezone(tz_name)
        fake_dt = datetime(2026, 3, 26, hour, 0, tzinfo=tz)
        return fake_dt

    def test_breakfast_hour(self):
        user = make_user()
        with patch("bot.services.menu_service.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_hour(8)
            result = _get_meal_time(user)
        assert result == "breakfast"

    def test_lunch_hour(self):
        user = make_user()
        with patch("bot.services.menu_service.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_hour(13)
            result = _get_meal_time(user)
        assert result == "lunch"

    def test_dinner_hour(self):
        user = make_user()
        with patch("bot.services.menu_service.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_hour(19)
            result = _get_meal_time(user)
        assert result == "dinner"

    def test_snack_late_evening(self):
        user = make_user()
        with patch("bot.services.menu_service.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_hour(22)
            result = _get_meal_time(user)
        assert result == "snack"

    def test_snack_early_morning(self):
        user = make_user()
        with patch("bot.services.menu_service.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_hour(4)
            result = _get_meal_time(user)
        assert result == "snack"


class TestFilterByAllergies:

    def test_no_allergies_returns_all(self):
        meals = [
            ("Куриная грудка", 200, 40, 3, 0),
            ("Творог", 100, 14, 4, 4),
        ]
        result = _filter_by_allergies(meals, None)
        assert len(result) == 2

    def test_filters_matching_meal(self):
        meals = [
            ("Омлет из яиц", 200, 14, 15, 2),
            ("Куриная грудка", 200, 40, 3, 0),
        ]
        result = _filter_by_allergies(meals, "яйца")
        assert len(result) == 1
        assert result[0][0] == "Куриная грудка"

    def test_returns_all_if_everything_filtered(self):
        # Если все блюда содержат аллерген — возвращаем всё (не оставляем пустым)
        meals = [
            ("Творог", 100, 14, 4, 4),
            ("Творог с ягодами", 140, 12, 4, 12),
        ]
        result = _filter_by_allergies(meals, "творог")
        assert len(result) == 2  # вернули всё, ничего не нашли без аллергена

    def test_multiple_allergens(self):
        meals = [
            ("Омлет из яиц", 200, 14, 15, 2),
            ("Творог 200г", 190, 22, 6, 8),
            ("Куриная грудка", 200, 40, 3, 0),
        ]
        result = _filter_by_allergies(meals, "яйца, творог")
        assert len(result) == 1
        assert "курин" in result[0][0].lower()


class TestSuggestQuick:

    def test_returns_string(self):
        user = make_user()
        with patch("bot.services.menu_service.datetime") as mock_dt:
            tz = pytz.timezone("Europe/Moscow")
            mock_dt.now.return_value = datetime(2026, 3, 26, 12, 0, tzinfo=tz)
            result = svc.suggest_quick(user, 600, 40)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_contains_meal_info(self):
        user = make_user()
        with patch("bot.services.menu_service.datetime") as mock_dt:
            tz = pytz.timezone("Europe/Moscow")
            mock_dt.now.return_value = datetime(2026, 3, 26, 8, 0, tzinfo=tz)
            result = svc.suggest_quick(user, 400, 25)
        assert "Ккал" in result or "ккал" in result
        assert "Белки" in result or "белк" in result.lower()

    def test_low_remaining_calories(self):
        user = make_user()
        with patch("bot.services.menu_service.datetime") as mock_dt:
            tz = pytz.timezone("Europe/Moscow")
            mock_dt.now.return_value = datetime(2026, 3, 26, 21, 0, tzinfo=tz)
            # Осталось только 150 ккал
            result = svc.suggest_quick(user, 150, 0)
        assert isinstance(result, str)

    def test_html_tags_present(self):
        user = make_user()
        with patch("bot.services.menu_service.datetime") as mock_dt:
            tz = pytz.timezone("Europe/Moscow")
            mock_dt.now.return_value = datetime(2026, 3, 26, 13, 0, tzinfo=tz)
            result = svc.suggest_quick(user, 500, 30)
        assert "<b>" in result


class TestFormatDailyPlan:

    def test_zero_eaten(self):
        result = svc.format_daily_plan(2200, 165, 0, 0)
        assert "2200" in result
        assert "165" in result
        assert "0%" in result

    def test_half_eaten(self):
        result = svc.format_daily_plan(2200, 165, 1100, 80)
        assert "50%" in result
        assert "1100" in result

    def test_full_eaten(self):
        result = svc.format_daily_plan(2200, 165, 2200, 165)
        assert "100%" in result
        # Остаток должен быть 0
        assert "0 ккал" in result or "0.0" in result or "0 к" in result

    def test_returns_html(self):
        result = svc.format_daily_plan(2000, 150, 800, 60)
        assert "<b>" in result
        assert "📊" in result
