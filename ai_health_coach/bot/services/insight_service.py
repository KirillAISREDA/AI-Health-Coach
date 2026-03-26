"""
InsightService — проактивные инсайты.

Каждое утро Celery вызывает generate_morning_insight() для каждого пользователя.
Сервис анализирует вчерашний день и формирует персонализированное сообщение:

  «Вчера ты не добрал 42г белка. Хочешь, добавлю яйца в завтрак?»
  «Отличный день — выполнил норму по воде и белку! 🔥»
  «Тренировка засчитана, но вода подкачала — выпил только 60%. Сегодня навёрстаем?»

Инсайт строится локально (без GPT) для быстрых случаев,
и через GPT для развёрнутого совета когда нужно.
"""

import logging
from datetime import date, timedelta
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import User, FoodLog, WaterLog
from bot.models.sleep import SleepLog
from bot.models.workout_log import WorkoutLog
from bot.utils.timezone import local_today, get_user_timezone

logger = logging.getLogger(__name__)


@dataclass
class DayStats:
    """Статистика за один день."""
    date: date

    # Питание
    calories: float = 0
    protein_g: float = 0
    fat_g: float = 0
    carbs_g: float = 0
    food_entries: int = 0

    # Вода
    water_ml: float = 0

    # Сон
    sleep_hours: Optional[float] = None
    sleep_quality: Optional[int] = None

    # Тренировка
    workout_done: bool = False
    workout_completed: Optional[str] = None  # full/partial/skip

    # Цели пользователя
    tdee_goal: float = 2000
    water_goal: float = 2000
    protein_goal: float = 150

    @property
    def calorie_pct(self) -> int:
        return int(self.calories / self.tdee_goal * 100) if self.tdee_goal else 0

    @property
    def water_pct(self) -> int:
        return int(self.water_ml / self.water_goal * 100) if self.water_goal else 0

    @property
    def protein_pct(self) -> int:
        return int(self.protein_g / self.protein_goal * 100) if self.protein_goal else 0

    @property
    def calorie_deficit(self) -> float:
        return max(0, self.tdee_goal - self.calories)

    @property
    def protein_deficit_g(self) -> float:
        return max(0, self.protein_goal - self.protein_g)

    @property
    def water_deficit_ml(self) -> float:
        return max(0, self.water_goal - self.water_ml)


class InsightService:

    async def get_day_stats(
        self,
        session: AsyncSession,
        user: User,
        target_date: Optional[date] = None,
    ) -> DayStats:
        """Загружает статистику за указанный день (default: вчера по TZ пользователя)."""
        if target_date is None:
            today = local_today(user)
            target_date = today - timedelta(days=1)

        tdee = user.tdee_kcal or 2000
        water_goal = user.water_goal_ml or 2000
        # Белок: 30% от TDEE
        protein_goal = tdee * 0.3 / 4

        stats = DayStats(
            date=target_date,
            tdee_goal=tdee,
            water_goal=water_goal,
            protein_goal=protein_goal,
        )

        # ── Питание ──────────────────────────────────────────────────────────
        food_res = await session.execute(
            select(
                func.coalesce(func.sum(FoodLog.calories), 0).label("cal"),
                func.coalesce(func.sum(FoodLog.protein_g), 0).label("prot"),
                func.coalesce(func.sum(FoodLog.fat_g), 0).label("fat"),
                func.coalesce(func.sum(FoodLog.carbs_g), 0).label("carbs"),
                func.count(FoodLog.id).label("entries"),
            ).where(
                and_(FoodLog.user_id == user.id, FoodLog.meal_date == target_date)
            )
        )
        row = food_res.one()
        stats.calories     = row.cal or 0
        stats.protein_g    = row.prot or 0
        stats.fat_g        = row.fat or 0
        stats.carbs_g      = row.carbs or 0
        stats.food_entries = row.entries or 0

        # ── Вода ─────────────────────────────────────────────────────────────
        water_res = await session.execute(
            select(func.coalesce(func.sum(WaterLog.amount_ml), 0)).where(
                and_(WaterLog.user_id == user.id, WaterLog.log_date == target_date)
            )
        )
        stats.water_ml = water_res.scalar() or 0

        # ── Сон ──────────────────────────────────────────────────────────────
        sleep_res = await session.execute(
            select(SleepLog).where(
                and_(SleepLog.user_id == user.id, SleepLog.log_date == target_date)
            )
        )
        sleep_log = sleep_res.scalar_one_or_none()
        if sleep_log:
            stats.sleep_hours   = sleep_log.sleep_hours
            stats.sleep_quality = sleep_log.quality_score

        # ── Тренировка ───────────────────────────────────────────────────────
        workout_res = await session.execute(
            select(WorkoutLog).where(
                and_(WorkoutLog.user_id == user.id, WorkoutLog.log_date == target_date)
            )
        )
        workout_log = workout_res.scalar_one_or_none()
        if workout_log:
            stats.workout_done      = True
            stats.workout_completed = workout_log.completed

        return stats

    def build_morning_message(self, user: User, stats: DayStats) -> str:
        """
        Строит утреннее сообщение без GPT.
        Быстро, всегда работает, персонализировано.
        """
        name = user.first_name or "чемпион"
        lines = [f"☀️ Доброе утро, {name}!\n"]

        # ── Итог вчера ───────────────────────────────────────────────────────
        if stats.food_entries == 0 and stats.water_ml == 0:
            lines.append("📋 Вчера ты ничего не записывал — это нормально.\nЗапишем сегодня? 💪\n")
            lines += self._build_today_goals(stats)
            return "\n".join(lines)

        lines.append("📊 <b>Итоги вчера:</b>")

        # Калории
        if stats.calories > 0:
            cal_emoji = "✅" if 85 <= stats.calorie_pct <= 115 else ("⬆️" if stats.calorie_pct > 115 else "⬇️")
            lines.append(
                f"{cal_emoji} Калории: <b>{stats.calories:.0f}</b> / {stats.tdee_goal:.0f} ккал "
                f"({stats.calorie_pct}%)"
            )

        # Белок
        if stats.protein_g > 0:
            prot_emoji = "✅" if stats.protein_pct >= 85 else "⚠️"
            lines.append(
                f"{prot_emoji} Белок: <b>{stats.protein_g:.0f}г</b> / {stats.protein_goal:.0f}г "
                f"({stats.protein_pct}%)"
            )

        # Вода
        water_emoji = "✅" if stats.water_pct >= 100 else ("⚠️" if stats.water_pct >= 60 else "❌")
        lines.append(
            f"{water_emoji} Вода: <b>{stats.water_ml:.0f}</b> / {stats.water_goal:.0f} мл "
            f"({stats.water_pct}%)"
        )

        # Тренировка
        if stats.workout_done:
            wt_label = {"full": "✅ выполнена", "partial": "⚡ частично", "skip": "❌ пропущена"}
            lines.append(f"🏋️ Тренировка: {wt_label.get(stats.workout_completed, '—')}")

        # Сон
        if stats.sleep_hours:
            sleep_emoji = "✅" if stats.sleep_hours >= 7 else ("⚠️" if stats.sleep_hours >= 6 else "❌")
            lines.append(f"{sleep_emoji} Сон: <b>{stats.sleep_hours}ч</b>")

        lines.append("")

        # ── Проактивный совет ────────────────────────────────────────────────
        advice = self._build_proactive_advice(stats)
        if advice:
            lines.append(advice)
            lines.append("")

        # ── Цели на сегодня ──────────────────────────────────────────────────
        lines += self._build_today_goals(stats)

        return "\n".join(lines)

    def _build_proactive_advice(self, stats: DayStats) -> Optional[str]:
        """Самый важный инсайт — одно чёткое действие."""

        # Критический недобор белка (< 70%) — только если были записи
        if stats.protein_pct < 70 and stats.food_entries > 0:
            deficit = stats.protein_deficit_g
            return (
                f"💡 <b>Совет:</b> вчера не хватило "
                f"<b>{deficit:.0f}г белка</b>. "
                f"Добавь сегодня яйца на завтрак (+12г) "
                f"или творог на перекус (+15г)."
            )

        # Мало воды (< 60%)
        if stats.water_pct < 60:
            deficit_l = stats.water_deficit_ml / 1000
            return (
                f"💧 <b>Совет:</b> вчера выпил только {stats.water_pct}% нормы. "
                f"Сегодня начни с большого стакана сразу после пробуждения "
                f"и держи бутылку рядом — нужно ещё {deficit_l:.1f}л."
            )

        # Плохой сон + сегодня надо аккуратно
        if stats.sleep_quality and stats.sleep_quality <= 2:
            return (
                f"😴 <b>Совет:</b> вчера спал плохо. "
                f"Сегодня лучше лёгкая тренировка или прогулка — "
                f"не насилуй организм. Ляг на 30-60 мин раньше обычного."
            )

        # Перебор калорий (> 120%)
        if stats.calorie_pct > 120 and stats.food_entries > 0:
            excess = stats.calories - stats.tdee_goal
            return (
                f"⚖️ <b>Совет:</b> вчера вышло чуть больше нормы "
                f"(+{excess:.0f} ккал). Сегодня без строгих ограничений — "
                f"просто больше овощей и воды."
            )

        # Отличный день!
        all_good = (
            stats.calorie_pct >= 85 and
            stats.protein_pct >= 85 and
            stats.water_pct >= 90
        )
        if all_good:
            return (
                f"🔥 <b>Вчера был отличный день!</b> "
                f"Белок, калории и вода — всё по плану. "
                f"Держи темп!"
            )

        return None

    def _build_today_goals(self, stats: DayStats) -> list[str]:
        """Напоминание о целях на сегодня."""
        return [
            "🎯 <b>Цели на сегодня:</b>",
            f"├ 🔥 {stats.tdee_goal:.0f} ккал",
            f"├ 🥩 {stats.protein_goal:.0f}г белка",
            f"└ 💧 {stats.water_goal:.0f} мл воды",
        ]

    async def build_morning_message_with_ai(
        self,
        session: AsyncSession,
        user: User,
        stats: DayStats,
    ) -> str:
        """
        Расширенная версия: локальное сообщение + короткий AI-комментарий.
        Используется когда есть интересный паттерн (3+ дня подряд не добирает белок и т.д.)
        """
        from bot.services.ai_service import ai_service

        # Строим базовое сообщение
        base = self.build_morning_message(user, stats)

        # Проверяем паттерны за 3 дня
        pattern = await self._check_3day_pattern(session, user)
        if not pattern:
            return base

        # Добавляем AI-инсайт для устойчивых паттернов
        prompt = (
            f"Дай ОДНУ короткую рекомендацию (1-2 предложения) по этому паттерну:\n"
            f"{pattern}\n\n"
            f"Формат: начни с эмодзи, будь конкретным и мотивирующим. "
            f"Без дисклеймеров в этом ответе."
        )

        try:
            profile = {
                "gender": user.gender, "age": user.age,
                "weight_kg": user.weight_kg, "goal": user.goal,
                "timezone": user.timezone,
            }
            ai_tip = await ai_service.chat(
                user_id=user.id,
                user_message=prompt,
                user_profile=profile,
                save_context=False,
            )
            return base + f"\n\n🤖 <b>Паттерн:</b> {ai_tip}"
        except Exception as e:
            logger.warning(f"AI insight failed for user {user.id}: {e}")
            return base

    async def _check_3day_pattern(
        self, session: AsyncSession, user: User
    ) -> Optional[str]:
        """Обнаруживает устойчивые паттерны за последние 3 дня."""
        today = local_today(user)
        days = [today - timedelta(days=i) for i in range(1, 4)]

        tdee = user.tdee_kcal or 2000
        protein_goal = tdee * 0.3 / 4
        water_goal = user.water_goal_ml or 2000

        low_protein_days = 0
        low_water_days   = 0
        no_log_days      = 0

        for d in days:
            food_res = await session.execute(
                select(
                    func.coalesce(func.sum(FoodLog.protein_g), 0).label("prot"),
                    func.count(FoodLog.id).label("entries"),
                ).where(and_(FoodLog.user_id == user.id, FoodLog.meal_date == d))
            )
            row = food_res.one()

            water_res = await session.execute(
                select(func.coalesce(func.sum(WaterLog.amount_ml), 0)).where(
                    and_(WaterLog.user_id == user.id, WaterLog.log_date == d)
                )
            )
            water = water_res.scalar() or 0

            if row.entries == 0:
                no_log_days += 1
            else:
                if row.prot < protein_goal * 0.7:
                    low_protein_days += 1
                if water < water_goal * 0.6:
                    low_water_days += 1

        if low_protein_days >= 3:
            return f"3 дня подряд недобор белка (цель {protein_goal:.0f}г/день)"
        if low_water_days >= 3:
            return f"3 дня подряд недобор воды (цель {water_goal:.0f}мл/день)"
        if no_log_days >= 3:
            return "3 дня подряд нет записей в дневнике"

        return None


insight_service = InsightService()
