from datetime import datetime, date
from typing import Optional
from sqlalchemy import (
    BigInteger, String, Float, Integer, Boolean,
    DateTime, Date, Text, ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import enum


class Base(DeclarativeBase):
    pass


# ─── Enums ────────────────────────────────────────────────────────────────────

class GoalType(str, enum.Enum):
    LOSE_WEIGHT  = "lose_weight"
    GAIN_MUSCLE  = "gain_muscle"
    MAINTAIN     = "maintain"
    RECOMPOSITION = "recomposition"


class ActivityLevel(str, enum.Enum):
    SEDENTARY    = "sedentary"      # офис, нет тренировок
    LIGHT        = "light"          # 1-2 тренировки/нед
    MODERATE     = "moderate"       # 3-4 тренировки/нед
    ACTIVE       = "active"         # 5+ тренировок/нед
    VERY_ACTIVE  = "very_active"    # спортсмен


class Gender(str, enum.Enum):
    MALE   = "male"
    FEMALE = "female"


class OnboardingStep(str, enum.Enum):
    START       = "start"
    GENDER      = "gender"
    AGE         = "age"
    HEIGHT      = "height"
    WEIGHT      = "weight"
    GOAL        = "goal"
    ACTIVITY    = "activity"
    ALLERGIES   = "allergies"
    TIMEZONE    = "timezone"
    DONE        = "done"


# ─── User ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user_id
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(64))

    # Физические параметры
    gender: Mapped[Optional[str]] = mapped_column(String(10))
    age: Mapped[Optional[int]] = mapped_column(Integer)
    height_cm: Mapped[Optional[float]] = mapped_column(Float)
    weight_kg: Mapped[Optional[float]] = mapped_column(Float)

    # Цели и активность
    goal: Mapped[Optional[str]] = mapped_column(String(20))
    activity_level: Mapped[Optional[str]] = mapped_column(String(20))

    # Дополнительно
    allergies: Mapped[Optional[str]] = mapped_column(Text)  # через запятую
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/Moscow")

    # Онбординг
    onboarding_step: Mapped[str] = mapped_column(
        String(20), default=OnboardingStep.START.value
    )
    onboarding_done: Mapped[bool] = mapped_column(Boolean, default=False)

    # Расчётные значения (кэшируются после онбординга)
    tdee_kcal: Mapped[Optional[float]] = mapped_column(Float)
    water_goal_ml: Mapped[Optional[float]] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    food_logs: Mapped[list["FoodLog"]] = relationship(back_populates="user")
    water_logs: Mapped[list["WaterLog"]] = relationship(back_populates="user")
    supplement_logs: Mapped[list["SupplementLog"]] = relationship(back_populates="user")
    supplements: Mapped[list["Supplement"]] = relationship(back_populates="user")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="user")


# ─── Food Log ─────────────────────────────────────────────────────────────────

class FoodLog(Base):
    __tablename__ = "food_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))

    logged_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    meal_date: Mapped[date] = mapped_column(Date, default=date.today)

    # Описание
    raw_input: Mapped[str] = mapped_column(Text)          # что написал/отправил юзер
    description: Mapped[Optional[str]] = mapped_column(Text)  # расшифровка от AI

    # КБЖУ
    calories: Mapped[Optional[float]] = mapped_column(Float)
    protein_g: Mapped[Optional[float]] = mapped_column(Float)
    fat_g: Mapped[Optional[float]] = mapped_column(Float)
    carbs_g: Mapped[Optional[float]] = mapped_column(Float)

    # Источник ввода
    is_photo: Mapped[bool] = mapped_column(Boolean, default=False)
    photo_file_id: Mapped[Optional[str]] = mapped_column(String(256))
    # Вес подтверждён пользователем вручную
    weight_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    weight_g: Mapped[Optional[float]] = mapped_column(Float)

    user: Mapped["User"] = relationship(back_populates="food_logs")


# ─── Water Log ────────────────────────────────────────────────────────────────

class WaterLog(Base):
    __tablename__ = "water_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))

    logged_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    log_date: Mapped[date] = mapped_column(Date, default=date.today)
    amount_ml: Mapped[int] = mapped_column(Integer)

    user: Mapped["User"] = relationship(back_populates="water_logs")


# ─── Supplements ─────────────────────────────────────────────────────────────

class Supplement(Base):
    """Справочник БАДов конкретного пользователя."""
    __tablename__ = "supplements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))

    name: Mapped[str] = mapped_column(String(128))
    dose: Mapped[Optional[str]] = mapped_column(String(64))     # "500 мг"
    schedule_time: Mapped[Optional[str]] = mapped_column(String(10))  # "08:00"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="supplements")
    logs: Mapped[list["SupplementLog"]] = relationship(back_populates="supplement")


class SupplementLog(Base):
    """Факт приёма БАД."""
    __tablename__ = "supplement_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    supplement_id: Mapped[int] = mapped_column(Integer, ForeignKey("supplements.id"))

    logged_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    log_date: Mapped[date] = mapped_column(Date, default=date.today)
    taken: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="supplement_logs")
    supplement: Mapped["Supplement"] = relationship(back_populates="logs")


# ─── Reminders ───────────────────────────────────────────────────────────────

class ReminderType(str, enum.Enum):
    WATER       = "water"
    SUPPLEMENT  = "supplement"
    SLEEP       = "sleep"
    WORKOUT     = "workout"


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))

    reminder_type: Mapped[str] = mapped_column(String(20))
    time_utc: Mapped[str] = mapped_column(String(10))   # "06:30" в UTC
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    supplement_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("supplements.id"), nullable=True)

    user: Mapped["User"] = relationship(back_populates="reminders")
