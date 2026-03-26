from datetime import datetime, timezone, date, timezone
from typing import Optional
from sqlalchemy import BigInteger, Integer, Float, String, DateTime, Date, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from bot.models import Base


class WorkoutLog(Base):
    __tablename__ = "workout_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))

    log_date: Mapped[date] = mapped_column(Date, default=date.today)
    logged_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Параметры тренировки
    feeling: Mapped[Optional[str]] = mapped_column(String(20))    # great/tired/sleepy/injury
    equipment: Mapped[Optional[str]] = mapped_column(String(20))  # none/home/gym/outdoor
    duration_min: Mapped[Optional[int]] = mapped_column(Integer)  # плановая длительность

    # Результат
    completed: Mapped[Optional[str]] = mapped_column(String(20))  # full/partial/skip
    # Добавленная вода за тренировку (автоматически)
    water_bonus_ml: Mapped[int] = mapped_column(Integer, default=0)

    # AI-план (первые 1000 символов для истории)
    plan_preview: Mapped[Optional[str]] = mapped_column(Text)

    # Зона поражения (если была травма)
    injury_zone: Mapped[Optional[str]] = mapped_column(String(64))
