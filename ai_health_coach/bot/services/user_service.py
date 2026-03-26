"""
User Service — работа с профилем пользователя.

Включает:
- CRUD пользователей
- Расчёт TDEE по формуле Миффлина-Сан Жеора
- Расчёт нормы воды
"""

import math
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import User, FoodLog, WaterLog, SupplementLog, OnboardingStep

logger = logging.getLogger(__name__)


# --- Activity multipliers ------------------------------------------------

ACTIVITY_MULTIPLIER = {
    "sedentary":   1.2,
    "light":       1.375,
    "moderate":    1.55,
    "active":      1.725,
    "very_active": 1.9,
}

GOAL_ADJUSTMENT = {
    "lose_weight":    -500,   # дефицит -500 ккал
    "gain_muscle":    +300,   # профицит +300 ккал
    "maintain":       0,
    "recomposition":  -200,   # небольшой дефицит
}


def calculate_tdee(
    gender: str,
    age: int,
    height_cm: float,
    weight_kg: float,
    activity_level: str,
    goal: str,
) -> float:
    """
    Формула Миффлина-Сан Жеора.
    Возвращает целевое кол-во ккал с учётом цели.
    """
    if gender == "male":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    else:
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161

    multiplier = ACTIVITY_MULTIPLIER.get(activity_level, 1.2)
    tdee = bmr * multiplier
    adjustment = GOAL_ADJUSTMENT.get(goal, 0)

    return round(tdee + adjustment, 0)


def calculate_water_goal(weight_kg: float) -> float:
    """30 мл x вес (кг). Тренировки добавляются динамически."""
    return round(weight_kg * 30, 0)


class UserService:

    async def get_or_create(self, session: AsyncSession, telegram_id: int, **kwargs) -> User:
        user = await self.get(session, telegram_id)
        if not user:
            user = User(id=telegram_id, **kwargs)
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user

    async def get(self, session: AsyncSession, telegram_id: int) -> Optional[User]:
        result = await session.execute(select(User).where(User.id == telegram_id))
        return result.scalar_one_or_none()

    async def update(self, session: AsyncSession, user: User, **kwargs) -> User:
        for key, value in kwargs.items():
            setattr(user, key, value)
        await session.commit()
        await session.refresh(user)
        return user

    async def complete_onboarding(self, session: AsyncSession, user: User) -> User:
        """Финализирует онбординг: считает TDEE и норму воды."""
        if all([user.gender, user.age, user.height_cm, user.weight_kg,
                user.goal, user.activity_level]):
            tdee = calculate_tdee(
                gender=user.gender,
                age=user.age,
                height_cm=user.height_cm,
                weight_kg=user.weight_kg,
                activity_level=user.activity_level,
                goal=user.goal,
            )
            water = calculate_water_goal(user.weight_kg)

            return await self.update(
                session, user,
                tdee_kcal=tdee,
                water_goal_ml=water,
                onboarding_step=OnboardingStep.DONE.value,
                onboarding_done=True,
            )
        return user

    def to_profile_dict(self, user: User) -> dict:
        """Словарь для передачи в AI service."""
        return {
            "gender": user.gender,
            "age": user.age,
            "weight_kg": user.weight_kg,
            "height_cm": user.height_cm,
            "goal": user.goal,
            "activity_level": user.activity_level,
            "tdee_kcal": user.tdee_kcal,
            "water_goal_ml": user.water_goal_ml,
            "allergies": user.allergies,
        }

    # -- Daily stats -------------------------------------------------------

    async def get_today_nutrition(self, session: AsyncSession, user_id: int) -> dict:
        today = date.today()
        result = await session.execute(
            select(
                func.coalesce(func.sum(FoodLog.calories), 0).label("calories"),
                func.coalesce(func.sum(FoodLog.protein_g), 0).label("protein"),
                func.coalesce(func.sum(FoodLog.fat_g), 0).label("fat"),
                func.coalesce(func.sum(FoodLog.carbs_g), 0).label("carbs"),
            ).where(
                and_(FoodLog.user_id == user_id, FoodLog.meal_date == today)
            )
        )
        row = result.one()
        return {"calories": row.calories, "protein": row.protein,
                "fat": row.fat, "carbs": row.carbs}

    async def get_today_water(self, session: AsyncSession, user_id: int) -> int:
        today = date.today()
        result = await session.execute(
            select(func.coalesce(func.sum(WaterLog.amount_ml), 0)).where(
                and_(WaterLog.user_id == user_id, WaterLog.log_date == today)
            )
        )
        return result.scalar()

    async def get_week_stats(self, session: AsyncSession, user_id: int) -> dict:
        week_ago = date.today() - timedelta(days=7)
        nut = await session.execute(
            select(
                func.coalesce(func.sum(FoodLog.calories), 0),
                func.coalesce(func.sum(FoodLog.protein_g), 0),
            ).where(
                and_(FoodLog.user_id == user_id, FoodLog.meal_date >= week_ago)
            )
        )
        row = nut.one()
        water = await session.execute(
            select(func.coalesce(func.sum(WaterLog.amount_ml), 0)).where(
                and_(WaterLog.user_id == user_id, WaterLog.log_date >= week_ago)
            )
        )
        return {
            "total_calories": row[0],
            "total_protein_g": row[1],
            "total_water_ml": water.scalar(),
            "days": 7,
        }


user_service = UserService()
