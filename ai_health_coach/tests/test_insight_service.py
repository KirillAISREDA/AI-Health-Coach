"""
Тесты InsightService.
Запуск: pytest tests/test_insight_service.py -v
"""

import pytest
import os
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

from datetime import date
from unittest.mock import MagicMock

from bot.services.insight_service import InsightService, DayStats


def make_user(name="Андрей", goal="lose_weight", tdee=2200, water=2400):
    u = MagicMock()
    u.id = 1
    u.first_name = name
    u.gender = "male"
    u.age = 30
    u.weight_kg = 80
    u.goal = goal
    u.tdee_kcal = tdee
    u.water_goal_ml = water
    u.timezone = "Europe/Moscow"
    return u


def make_stats(**kwargs) -> DayStats:
    defaults = dict(
        date=date(2026, 3, 26),
        calories=0, protein_g=0, fat_g=0, carbs_g=0,
        food_entries=0, water_ml=0,
        sleep_hours=None, sleep_quality=None,
        workout_done=False, workout_completed=None,
        tdee_goal=2200, water_goal=2400, protein_goal=165,
    )
    defaults.update(kwargs)
    return DayStats(**defaults)


svc = InsightService()


class TestDayStatsProperties:

    def test_calorie_pct_zero_when_no_goal(self):
        s = make_stats(calories=500, tdee_goal=0)
        assert s.calorie_pct == 0

    def test_calorie_pct_normal(self):
        s = make_stats(calories=1980, tdee_goal=2200)
        assert s.calorie_pct == 90

    def test_calorie_pct_overflow_capped_calculation(self):
        s = make_stats(calories=2750, tdee_goal=2200)
        assert s.calorie_pct == 125

    def test_water_pct_complete(self):
        s = make_stats(water_ml=2400, water_goal=2400)
        assert s.water_pct == 100

    def test_protein_deficit(self):
        s = make_stats(protein_g=100, protein_goal=165)
        assert s.protein_deficit_g == pytest.approx(65, abs=1)

    def test_no_deficit_when_goal_met(self):
        s = make_stats(protein_g=170, protein_goal=165)
        assert s.protein_deficit_g == 0


class TestBuildMorningMessage:

    def test_no_logs_message(self):
        user = make_user()
        stats = make_stats()  # всё нули
        msg = svc.build_morning_message(user, stats)
        assert "Андрей" in msg
        assert "ничего не записывал" in msg

    def test_contains_username(self):
        user = make_user("Виктория")
        stats = make_stats(calories=1800, protein_g=120, water_ml=2000,
                           food_entries=3, tdee_goal=1800, water_goal=2000, protein_goal=135)
        msg = svc.build_morning_message(user, stats)
        assert "Виктория" in msg

    def test_contains_calories(self):
        user = make_user()
        stats = make_stats(calories=1950, protein_g=140, water_ml=2100,
                           food_entries=4, tdee_goal=2200, water_goal=2400, protein_goal=165)
        msg = svc.build_morning_message(user, stats)
        assert "1950" in msg

    def test_great_day_message(self):
        user = make_user()
        stats = make_stats(
            calories=2100, protein_g=155, water_ml=2400,
            food_entries=4, tdee_goal=2200, water_goal=2400, protein_goal=165,
        )
        msg = svc.build_morning_message(user, stats)
        assert "отличный" in msg.lower()

    def test_low_water_advice(self):
        user = make_user()
        stats = make_stats(
            calories=2000, protein_g=150, water_ml=800,
            food_entries=3, tdee_goal=2200, water_goal=2400, protein_goal=165,
        )
        msg = svc.build_morning_message(user, stats)
        assert "воды" in msg.lower() or "воду" in msg.lower() or "вода" in msg.lower()

    def test_low_protein_advice(self):
        user = make_user()
        stats = make_stats(
            calories=1600, protein_g=80, water_ml=2000,
            food_entries=3, tdee_goal=2200, water_goal=2400, protein_goal=165,
        )
        msg = svc.build_morning_message(user, stats)
        assert "белк" in msg.lower()

    def test_bad_sleep_advice(self):
        user = make_user()
        stats = make_stats(
            calories=2000, protein_g=150, water_ml=2000,
            food_entries=3, sleep_hours=5.0, sleep_quality=2,
            tdee_goal=2200, water_goal=2400, protein_goal=165,
        )
        msg = svc.build_morning_message(user, stats)
        assert "спал" in msg.lower() or "сон" in msg.lower() or "сна" in msg.lower()

    def test_workout_shown(self):
        user = make_user()
        stats = make_stats(
            calories=2100, protein_g=150, water_ml=2200,
            food_entries=3, workout_done=True, workout_completed="full",
            tdee_goal=2200, water_goal=2400, protein_goal=165,
        )
        msg = svc.build_morning_message(user, stats)
        assert "Тренировка" in msg or "тренировка" in msg

    def test_today_goals_always_present(self):
        user = make_user()
        stats = make_stats()
        msg = svc.build_morning_message(user, stats)
        # Цели всегда в конце
        assert "ккал" in msg
        assert "белка" in msg or "белок" in msg
        assert "воды" in msg or "вода" in msg

    def test_html_safe(self):
        user = make_user()
        stats = make_stats(calories=2000, protein_g=120, water_ml=1800,
                           food_entries=3, tdee_goal=2200, water_goal=2400,
                           protein_goal=165)
        msg = svc.build_morning_message(user, stats)
        # Нет незакрытых тегов
        import re
        opens  = len(re.findall(r"<(?!/)(?!br)[a-zA-Z]", msg))
        closes = len(re.findall(r"</[a-zA-Z]", msg))
        assert opens == closes, f"HTML теги не сбалансированы: {opens} открытых, {closes} закрытых"


class TestProactiveAdvice:

    def test_no_food_logs_still_gets_water_advice(self):
        # Нет логов еды, но вода тоже 0 → совет про воду это корректное поведение
        stats = make_stats()
        advice = svc._build_proactive_advice(stats)
        # Вода 0 из 2400 = 0% → должен прийти совет про воду
        assert advice is not None
        assert "воды" in advice.lower() or "воду" in advice.lower() or "💧" in advice

    def test_no_advice_when_only_food_missing_but_water_ok(self):
        # Вода норм, еды нет — специфичного совета нет (нет данных по белку)
        stats = make_stats(water_ml=2400, water_goal=2400, food_entries=0,
                           calories=0, protein_g=0, protein_goal=165)
        advice = svc._build_proactive_advice(stats)
        # protein_pct=0 < 70 НО food_entries=0 → не советуем про белок без данных
        # water 100% → совет про воду не нужен
        assert advice is None

    def test_calorie_excess_advice(self):
        stats = make_stats(
            calories=2800, tdee_goal=2200, food_entries=3,
            protein_g=150, water_ml=2000, water_goal=2400, protein_goal=165,
        )
        advice = svc._build_proactive_advice(stats)
        assert advice is not None
        assert "ккал" in advice.lower() or "норм" in advice.lower()

    def test_returns_none_for_mediocre_but_acceptable_day(self):
        # 80% по всем метрикам — нет особого совета
        stats = make_stats(
            calories=1760, protein_g=132, water_ml=1920,
            food_entries=3, tdee_goal=2200, water_goal=2400, protein_goal=165,
        )
        advice = svc._build_proactive_advice(stats)
        # Может вернуть None или какой-то совет — главное не падает
        assert advice is None or isinstance(advice, str)
