"""
Тесты user_service.

Запуск: pytest tests/test_user_service.py -v
"""

import pytest
import os
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

from bot.services.user_service import calculate_tdee, calculate_water_goal


class TestTDEE:
    """Формула Миффлина-Сан Жеора."""

    def test_male_sedentary_maintain(self):
        # BMR = 10*80 + 6.25*180 - 5*30 + 5 = 800+1125-150+5 = 1780
        # TDEE = 1780 * 1.2 = 2136, goal=maintain -> +0 = 2136
        result = calculate_tdee("male", 30, 180, 80, "sedentary", "maintain")
        assert result == pytest.approx(2136, abs=2)

    def test_female_moderate_lose_weight(self):
        # BMR = 10*60 + 6.25*165 - 5*25 - 161 = 600+1031.25-125-161 = 1345.25
        # TDEE = 1345.25 * 1.55 = 2085.1, goal=lose -> -500 = 1585.1
        result = calculate_tdee("female", 25, 165, 60, "moderate", "lose_weight")
        assert result == pytest.approx(1585, abs=5)

    def test_male_active_gain_muscle(self):
        # BMR = 10*90 + 6.25*185 - 5*28 + 5 = 900+1156.25-140+5 = 1921.25
        # TDEE = 1921.25 * 1.725 = 3314.2, goal=gain -> +300 = 3614.2
        result = calculate_tdee("male", 28, 185, 90, "active", "gain_muscle")
        assert result == pytest.approx(3614, abs=5)

    def test_female_very_active_recomposition(self):
        result = calculate_tdee("female", 22, 170, 65, "very_active", "recomposition")
        # Проверяем что значение в разумном диапазоне
        assert 2000 <= result <= 4000

    def test_minimum_calories_not_too_low(self):
        # Худой пользователь, дефицит — должен клампнуться до минимума (1200 для женщин)
        result = calculate_tdee("female", 50, 155, 45, "sedentary", "lose_weight")
        assert result >= 1200, f"Женский минимум 1200 ккал, получили {result}"

    def test_male_minimum_floor(self):
        # Для мужчин минимум 1500 ккал
        result = calculate_tdee("male", 50, 160, 50, "sedentary", "lose_weight")
        assert result >= 1500, f"Мужской минимум 1500 ккал, получили {result}"

    def test_all_activity_levels_increase_tdee(self):
        """Более высокая активность -> больше калорий."""
        levels = ["sedentary", "light", "moderate", "active", "very_active"]
        results = [
            calculate_tdee("male", 30, 175, 75, level, "maintain")
            for level in levels
        ]
        for i in range(len(results) - 1):
            assert results[i] < results[i + 1], \
                f"{levels[i]} ({results[i]}) должен быть < {levels[i+1]} ({results[i+1]})"

    def test_goal_adjustments(self):
        """Разные цели дают разные калории."""
        base_params = ("male", 30, 175, 75, "moderate")
        lose   = calculate_tdee(*base_params, "lose_weight")
        maintain = calculate_tdee(*base_params, "maintain")
        gain   = calculate_tdee(*base_params, "gain_muscle")

        assert lose < maintain < gain

    def test_lose_weight_deficit_500(self):
        maintain = calculate_tdee("male", 30, 175, 75, "moderate", "maintain")
        lose     = calculate_tdee("male", 30, 175, 75, "moderate", "lose_weight")
        assert maintain - lose == pytest.approx(500, abs=1)

    def test_gain_muscle_surplus_300(self):
        maintain = calculate_tdee("male", 30, 175, 75, "moderate", "maintain")
        gain     = calculate_tdee("male", 30, 175, 75, "moderate", "gain_muscle")
        assert gain - maintain == pytest.approx(300, abs=1)


class TestWaterGoal:

    def test_basic_formula(self):
        # 30 мл * 80 кг = 2400
        assert calculate_water_goal(80) == pytest.approx(2400, abs=1)

    def test_light_person(self):
        assert calculate_water_goal(55) == pytest.approx(1650, abs=1)

    def test_heavy_person(self):
        assert calculate_water_goal(120) == pytest.approx(3600, abs=1)

    def test_result_is_float(self):
        # round(float * float, 0) возвращает float
        result = calculate_water_goal(75)
        assert isinstance(result, float), f"Ожидали float, получили {type(result)}"
        assert result == 2250.0

    def test_proportional(self):
        """Вода пропорциональна весу."""
        w1 = calculate_water_goal(60)
        w2 = calculate_water_goal(90)
        assert w2 / w1 == pytest.approx(90 / 60, rel=0.01)
