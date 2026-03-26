"""
SleepLog — запись о качестве сна.
Добавляется либо через утренний опрос, либо вручную.
"""

from datetime import datetime, timezone, date, timezone
from typing import Optional
from sqlalchemy import BigInteger, Integer, Float, DateTime, Date, Text, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.models import Base


class SleepLog(Base):
    __tablename__ = "sleep_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))

    log_date: Mapped[date] = mapped_column(Date, default=date.today)
    logged_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Данные о сне
    sleep_hours: Mapped[Optional[float]] = mapped_column(Float)       # 7.5
    quality_score: Mapped[Optional[int]] = mapped_column(Integer)     # 1-5
    bedtime: Mapped[Optional[str]] = mapped_column(Text)              # "23:30"
    wakeup_time: Mapped[Optional[str]] = mapped_column(Text)          # "07:00"
    notes: Mapped[Optional[str]] = mapped_column(Text)                # "просыпался в 3"
    affected_workout: Mapped[bool] = mapped_column(Boolean, default=False)
