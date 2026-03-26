"""
DB-level тесты user_service — используют реальный SQLite in-memory через conftest.
Запуск: pytest tests/test_user_service_db.py -v
"""

import pytest
import pytest_asyncio
import os
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

from datetime import date, timedelta
from bot.services.user_service import user_service
from bot.models import User, FoodLog, WaterLog


class TestUserServiceDB:

    @pytest.mark.asyncio
    async def test_get_or_create_new_user(self, session):
        user = await user_service.get_or_create(
            session, telegram_id=999, first_name="Test", username="testbot"
        )
        assert user.id == 999
        assert user.first_name == "Test"
        assert user.onboarding_done == False

    @pytest.mark.asyncio
    async def test_get_or_create_existing_user(self, session):
        u1 = await user_service.get_or_create(session, telegram_id=888, first_name="Alice")
        u2 = await user_service.get_or_create(session, telegram_id=888, first_name="Alice2")
        # Второй вызов возвращает существующего, имя не обновляется
        assert u1.id == u2.id
        assert u2.first_name == "Alice"

    @pytest.mark.asyncio
    async def test_update_user(self, session, db_user):
        updated = await user_service.update(session, db_user, weight_kg=75.5)
        assert updated.weight_kg == 75.5

    @pytest.mark.asyncio
    async def test_complete_onboarding_calculates_tdee(self, session):
        user = await user_service.get_or_create(
            session, telegram_id=777,
            gender="male", age=28, height_cm=180,
            weight_kg=80, goal="lose_weight",
            activity_level="moderate",
        )
        completed = await user_service.complete_onboarding(session, user)
        assert completed.onboarding_done == True
        assert completed.tdee_kcal is not None
        assert completed.tdee_kcal > 1500  # выше минимума
        assert completed.water_goal_ml is not None
        assert completed.water_goal_ml == 80 * 30  # 30мл * 80кг

    @pytest.mark.asyncio
    async def test_get_today_nutrition_empty(self, session, db_user):
        today = date.today()
        result = await user_service.get_today_nutrition(session, db_user.id, today)
        assert result["calories"] == 0
        assert result["protein"] == 0

    @pytest.mark.asyncio
    async def test_get_today_nutrition_with_logs(self, session, db_user):
        today = date.today()
        for _ in range(3):
            log = FoodLog(
                user_id=db_user.id,
                raw_input="test food",
                meal_date=today,
                calories=400.0,
                protein_g=30.0,
                fat_g=15.0,
                carbs_g=40.0,
            )
            session.add(log)
        await session.commit()

        result = await user_service.get_today_nutrition(session, db_user.id, today)
        assert result["calories"] == pytest.approx(1200.0, abs=1)
        assert result["protein"] == pytest.approx(90.0, abs=1)

    @pytest.mark.asyncio
    async def test_get_today_water_empty(self, session, db_user):
        today = date.today()
        result = await user_service.get_today_water(session, db_user.id, today)
        assert result == 0

    @pytest.mark.asyncio
    async def test_get_today_water_with_logs(self, session, db_user):
        today = date.today()
        for ml in [250, 500, 300]:
            session.add(WaterLog(user_id=db_user.id, log_date=today, amount_ml=ml))
        await session.commit()

        result = await user_service.get_today_water(session, db_user.id, today)
        assert result == 1050

    @pytest.mark.asyncio
    async def test_get_week_stats_spans_7_days(self, session, db_user, food_logs_week, water_logs_week):
        today = date.today()
        result = await user_service.get_week_stats(session, db_user.id, today)
        # Должны суммироваться данные за 7 дней
        assert result["total_calories"] > 0
        assert result["total_water_ml"] > 0
        assert result["days"] == 7

    @pytest.mark.asyncio
    async def test_to_profile_dict_includes_timezone(self, session, db_user):
        profile = user_service.to_profile_dict(db_user)
        assert "timezone" in profile
        assert profile["timezone"] == "Europe/Moscow"

    @pytest.mark.asyncio
    async def test_to_profile_dict_has_all_keys(self, session, db_user):
        profile = user_service.to_profile_dict(db_user)
        expected_keys = {
            "gender", "age", "weight_kg", "height_cm",
            "goal", "activity_level", "tdee_kcal",
            "water_goal_ml", "allergies", "timezone",
        }
        assert expected_keys.issubset(set(profile.keys()))
