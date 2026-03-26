from datetime import datetime, timezone, date, timezone
from typing import Optional
from sqlalchemy import BigInteger, Integer, Float, DateTime, Date, Text, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from bot.models import Base


class WeightLog(Base):
    """
    История взвешиваний.

    Пользователь вносит вес раз в неделю (или чаще).
    Используется для:
    - Отображения динамики в профиле
    - Пересчёта TDEE при значительном изменении
    - Мотивационных сообщений в еженедельном дайджесте
    """
    __tablename__ = "weight_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )

    log_date: Mapped[date] = mapped_column(Date, default=date.today)
    logged_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Автоматически обновлён TDEE после этого взвешивания
    tdee_updated: Mapped[bool] = mapped_column(Boolean, default=False)
