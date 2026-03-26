"""
ReportService — генерирует PDF-отчёт прогресса за неделю.

Структура PDF:
- Обложка с именем и датами
- Сводка по калориям (прогресс-бар по дням)
- Сводка по белкам / жирам / углеводам
- Водный баланс (по дням)
- Трекер сна (качество по дням)
- Трекер БАДов (% выполнения)
- Итоговый AI-комментарий коуча

Используется reportlab (уже в requirements.txt).
"""

import io
import logging
from datetime import date, timedelta
from typing import Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak
)
from reportlab.platypus import Flowable
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import User, FoodLog, WaterLog, SupplementLog, Supplement
from bot.models.sleep import SleepLog

logger = logging.getLogger(__name__)

# --- Палитра --------------------------------------------------------------
GREEN      = colors.HexColor("#00C896")
PURPLE     = colors.HexColor("#6C63FF")
RED_SOFT   = colors.HexColor("#FF6B6B")
YELLOW     = colors.HexColor("#FFD93D")
DARK       = colors.HexColor("#1A1F2E")
MID        = colors.HexColor("#4A5568")
LIGHT_BG   = colors.HexColor("#F4F7F6")
CARD_GREEN = colors.HexColor("#EFF9F5")
WHITE      = colors.white
BORDER     = colors.HexColor("#E2E8F0")

W, H = A4
MARGIN = 2 * cm
PAGE_W = W - 2 * MARGIN

DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


# --- Flowables ------------------------------------------------------------

class ColorBar(Flowable):
    """Горизонтальный прогресс-бар."""
    def __init__(self, width, filled_pct, color=GREEN, height=10, bg=LIGHT_BG):
        super().__init__()
        self.bar_width = width
        self.filled_pct = min(100, max(0, filled_pct))
        self.bar_color = color
        self.bar_height = height
        self.bg = bg
        self.width = width
        self.height = height + 2

    def draw(self):
        # Фон
        self.canv.setFillColor(self.bg)
        self.canv.roundRect(0, 0, self.bar_width, self.bar_height, 4, fill=1, stroke=0)
        # Заполнение
        filled = self.bar_width * self.filled_pct / 100
        if filled > 0:
            self.canv.setFillColor(self.bar_color)
            self.canv.roundRect(0, 0, filled, self.bar_height, 4, fill=1, stroke=0)


def S(name, **kw):
    return ParagraphStyle(name, **kw)


def page_header(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(GREEN)
    canvas.rect(0, H - 5, W, 5, fill=1, stroke=0)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MID)
    canvas.drawString(MARGIN, 14, "AI Health Coach — Персональный отчёт прогресса")
    canvas.drawRightString(W - MARGIN, 14, f"Стр. {doc.page}")
    canvas.restoreState()


# --- Helpers --------------------------------------------------------------

def metric_row(label: str, value: str, goal: str, pct: int, color=GREEN) -> list:
    bar = ColorBar(PAGE_W * 0.45, pct, color=color, height=8)
    row_data = [[
        Paragraph(label, S("ml", fontSize=9, textColor=MID, fontName="Helvetica")),
        Paragraph(f"<b>{value}</b>", S("mv", fontSize=9, textColor=DARK, fontName="Helvetica-Bold")),
        bar,
        Paragraph(f"{pct}%", S("mp", fontSize=9, textColor=color, fontName="Helvetica-Bold",
                               alignment=TA_RIGHT)),
    ]]
    t = Table(row_data, colWidths=[PAGE_W*0.25, PAGE_W*0.13, PAGE_W*0.50, PAGE_W*0.12])
    t.setStyle(TableStyle([
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
        ("LEFTPADDING",   (0,0),(-1,-1), 0),
        ("RIGHTPADDING",  (0,0),(-1,-1), 0),
    ]))
    return [t]


def section_header(title: str, color=GREEN) -> list:
    return [
        Spacer(1, 12),
        HRFlowable(width=PAGE_W*0.08, thickness=4, color=color, spaceBefore=0, spaceAfter=4),
        Paragraph(title, S("sh", fontSize=14, fontName="Helvetica-Bold",
                            textColor=DARK, spaceAfter=4)),
    ]


def small_table(data, col_widths, header_bg=DARK) -> Table:
    style = [
        ("BACKGROUND",    (0,0),(-1,0),  header_bg),
        ("TEXTCOLOR",     (0,0),(-1,0),  WHITE),
        ("FONTNAME",      (0,0),(-1,0),  "Helvetica-Bold"),
        ("FONTNAME",      (0,1),(-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,0),(-1,-1), 8),
        ("TEXTCOLOR",     (0,1),(-1,-1), MID),
        ("TOPPADDING",    (0,0),(-1,-1), 5),
        ("BOTTOMPADDING", (0,0),(-1,-1), 5),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("RIGHTPADDING",  (0,0),(-1,-1), 8),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, LIGHT_BG]),
        ("GRID",          (0,0),(-1,-1), 0.3, BORDER),
    ]
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle(style))
    return t


# --- Основной класс -------------------------------------------------------

class ReportService:

    async def generate_weekly_pdf(
        self,
        session: AsyncSession,
        user: User,
        ai_comment: Optional[str] = None,
    ) -> bytes:
        """
        Генерирует PDF-отчёт за последние 7 дней.
        Возвращает байты PDF-файла.
        """
        today = date.today()
        week_start = today - timedelta(days=6)
        days = [week_start + timedelta(days=i) for i in range(7)]

        # -- Загружаем данные ------------------------------------------------
        food_by_day  = await self._food_by_day(session, user.id, week_start, today)
        water_by_day = await self._water_by_day(session, user.id, week_start, today)
        sleep_by_day = await self._sleep_by_day(session, user.id, week_start, today)
        sup_stats    = await self._supplement_stats(session, user.id, week_start, today)

        tdee       = user.tdee_kcal or 2000
        water_goal = user.water_goal_ml or 2000

        # -- Строим PDF ------------------------------------------------------
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            leftMargin=MARGIN, rightMargin=MARGIN,
            topMargin=2.2*cm, bottomMargin=2*cm,
            title=f"Отчёт прогресса — {today.strftime('%d.%m.%Y')}",
        )

        story = []
        story += self._build_cover(user, week_start, today)
        story += self._build_nutrition_section(days, food_by_day, tdee)
        story += self._build_water_section(days, water_by_day, water_goal)
        story += self._build_sleep_section(days, sleep_by_day)
        if sup_stats:
            story += self._build_supplements_section(sup_stats)
        if ai_comment:
            story += self._build_ai_comment(ai_comment)

        doc.build(story, onFirstPage=page_header, onLaterPages=page_header)
        return buf.getvalue()

    # --- Секции -----------------------------------------------------------

    def _build_cover(self, user: User, week_start: date, today: date) -> list:
        name = user.first_name or "Атлет"
        period = f"{week_start.strftime('%d.%m')} — {today.strftime('%d.%m.%Y')}"

        goal_labels = {
            "lose_weight":    "Похудение", "gain_muscle":  "Набор массы",
            "maintain":       "Поддержание", "recomposition": "Рекомпозиция",
        }

        cover = Table([[
            Paragraph(
                f"<b>Отчёт прогресса</b><br/>"
                f"<font color='#00C896'>{name}</font><br/>"
                f"<font size='10' color='#718096'>{period}</font>",
                S("ct", fontSize=22, fontName="Helvetica-Bold", textColor=DARK,
                  leading=28, spaceAfter=0)
            ),
            Paragraph(
                f"<b>Параметры</b><br/>"
                f"Вес: {user.weight_kg or '—'} кг<br/>"
                f"TDEE: {int(user.tdee_kcal) if user.tdee_kcal else '—'} ккал<br/>"
                f"Цель: {goal_labels.get(user.goal, '—')}",
                S("cp", fontSize=9, fontName="Helvetica", textColor=MID, leading=14)
            ),
        ]], colWidths=[PAGE_W * 0.65, PAGE_W * 0.35])
        cover.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), CARD_GREEN),
            ("TOPPADDING",    (0,0),(-1,-1), 18),
            ("BOTTOMPADDING", (0,0),(-1,-1), 18),
            ("LEFTPADDING",   (0,0),(-1,-1), 16),
            ("RIGHTPADDING",  (0,0),(-1,-1), 16),
            ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
            ("BOX",           (0,0),(-1,-1), 2, GREEN),
        ]))
        return [cover, Spacer(1, 16)]

    def _build_nutrition_section(self, days, food_by_day, tdee) -> list:
        story = section_header("🥗 Питание по дням")

        header = ["День", "Дата", "Ккал", "Белки", "Жиры", "Углев.", "% от нормы"]
        rows = [header]
        total_cal = total_prot = total_fat = total_carb = 0

        for i, d in enumerate(days):
            data = food_by_day.get(d, {})
            cal   = data.get("calories", 0)
            prot  = data.get("protein",  0)
            fat   = data.get("fat",      0)
            carb  = data.get("carbs",    0)
            pct   = int(cal / tdee * 100) if tdee else 0
            total_cal  += cal
            total_prot += prot
            total_fat  += fat
            total_carb += carb

            pct_str = f"{pct}%" if cal > 0 else "—"
            rows.append([
                DAY_NAMES[d.weekday()],
                d.strftime("%d.%m"),
                f"{cal:.0f}" if cal else "—",
                f"{prot:.0f}г" if prot else "—",
                f"{fat:.0f}г" if fat else "—",
                f"{carb:.0f}г" if carb else "—",
                pct_str,
            ])

        avg_cal = total_cal / 7
        rows.append([
            "Итого", "7 дней",
            f"{total_cal:.0f}",
            f"{total_prot:.0f}г",
            f"{total_fat:.0f}г",
            f"{total_carb:.0f}г",
            f"{int(avg_cal/tdee*100) if tdee else 0}% avg",
        ])

        t = small_table(rows, [
            PAGE_W*0.09, PAGE_W*0.10, PAGE_W*0.12,
            PAGE_W*0.12, PAGE_W*0.12, PAGE_W*0.12, PAGE_W*0.33,
        ])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,-1),(-1,-1), CARD_GREEN),
            ("FONTNAME",   (0,-1),(-1,-1), "Helvetica-Bold"),
            ("TEXTCOLOR",  (0,-1),(-1,-1), DARK),
        ]))
        story.append(t)

        story.append(Spacer(1, 10))
        avg_pct = int(avg_cal / tdee * 100) if tdee else 0
        story += metric_row("Ккал (среднее/день)", f"{avg_cal:.0f} / {tdee:.0f}",
                             f"{tdee:.0f}", avg_pct, GREEN)
        protein_goal = tdee * 0.3 / 4
        prot_avg = total_prot / 7
        prot_pct = int(prot_avg / protein_goal * 100) if protein_goal else 0
        story += metric_row("Белок (среднее/день)", f"{prot_avg:.0f} / {protein_goal:.0f}г",
                             f"{protein_goal:.0f}", prot_pct, PURPLE)
        return story

    def _build_water_section(self, days, water_by_day, water_goal) -> list:
        story = section_header("💧 Водный баланс", PURPLE)

        header = ["День", "Дата", "Выпито (мл)", "Норма (мл)", "Выполнение"]
        rows = [header]
        total = 0

        for d in days:
            ml = water_by_day.get(d, 0)
            total += ml
            pct = int(ml / water_goal * 100) if water_goal else 0
            status = "✅" if pct >= 100 else ("⚠️" if pct >= 60 else "❌")
            rows.append([
                DAY_NAMES[d.weekday()],
                d.strftime("%d.%m"),
                f"{ml}" if ml else "0",
                f"{int(water_goal)}",
                f"{status} {pct}%",
            ])

        avg_ml = total / 7
        avg_pct = int(avg_ml / water_goal * 100) if water_goal else 0
        rows.append(["Среднее", "7 дней", f"{avg_ml:.0f}", f"{int(water_goal)}", f"{avg_pct}% avg"])

        t = small_table(rows, [
            PAGE_W*0.10, PAGE_W*0.10, PAGE_W*0.22, PAGE_W*0.22, PAGE_W*0.36
        ], header_bg=PURPLE)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,-1),(-1,-1), colors.HexColor("#F0EEFF")),
            ("FONTNAME",   (0,-1),(-1,-1), "Helvetica-Bold"),
        ]))
        story.append(t)
        story.append(Spacer(1, 8))
        story += metric_row("Вода (среднее/день)", f"{avg_ml:.0f} / {water_goal:.0f} мл",
                             f"{water_goal:.0f}", avg_pct, PURPLE)
        return story

    def _build_sleep_section(self, days, sleep_by_day) -> list:
        story = section_header("😴 Сон", YELLOW)

        has_data = any(sleep_by_day.get(d) for d in days)
        if not has_data:
            story.append(Paragraph(
                "Данные о сне за эту неделю не записаны.\n"
                "Используй кнопку «😴 Сон» в главном меню.",
                S("nb", fontSize=9, textColor=MID, fontName="Helvetica-Oblique")
            ))
            return story

        header = ["День", "Дата", "Часов", "Качество", "Заметка"]
        rows = [header]
        total_hours = count = 0

        for d in days:
            log = sleep_by_day.get(d)
            if log:
                stars = "⭐" * (log.quality_score or 0)
                rows.append([
                    DAY_NAMES[d.weekday()],
                    d.strftime("%d.%m"),
                    f"{log.sleep_hours or '—'}",
                    stars or "—",
                    (log.notes or "")[:30],
                ])
                if log.sleep_hours:
                    total_hours += log.sleep_hours
                    count += 1
            else:
                rows.append([DAY_NAMES[d.weekday()], d.strftime("%d.%m"), "—", "—", ""])

        if count:
            avg_h = total_hours / count
            rows.append(["Среднее", f"{count} дн.", f"{avg_h:.1f}ч", "", ""])

        t = small_table(rows, [
            PAGE_W*0.09, PAGE_W*0.10, PAGE_W*0.12, PAGE_W*0.20, PAGE_W*0.49
        ], header_bg=colors.HexColor("#B7791F"))
        story.append(t)
        return story

    def _build_supplements_section(self, sup_stats: list) -> list:
        story = section_header("💊 Приём БАДов", RED_SOFT)

        header = ["БАД", "Доза", "Принято", "Пропущено", "% выполнения"]
        rows = [header]

        for s in sup_stats:
            total = s["taken"] + s["skipped"]
            pct = int(s["taken"] / total * 100) if total else 0
            rows.append([
                s["name"],
                s["dose"] or "—",
                str(s["taken"]),
                str(s["skipped"]),
                f"{pct}%",
            ])

        t = small_table(rows, [
            PAGE_W*0.35, PAGE_W*0.18, PAGE_W*0.14, PAGE_W*0.14, PAGE_W*0.19
        ], header_bg=RED_SOFT)
        story.append(t)
        return story

    def _build_ai_comment(self, comment: str) -> list:
        story = section_header("🤖 Комментарий коуча", GREEN)
        box = Table(
            [[Paragraph(comment, S("ac", fontSize=9, textColor=DARK, fontName="Helvetica",
                                   leading=14, spaceAfter=0))]],
            colWidths=[PAGE_W]
        )
        box.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(-1,-1), CARD_GREEN),
            ("TOPPADDING",    (0,0),(-1,-1), 14),
            ("BOTTOMPADDING", (0,0),(-1,-1), 14),
            ("LEFTPADDING",   (0,0),(-1,-1), 14),
            ("RIGHTPADDING",  (0,0),(-1,-1), 14),
            ("BOX",           (0,0),(-1,-1), 1.5, GREEN),
        ]))
        story.append(box)
        story.append(Spacer(1, 10))
        story.append(Paragraph(
            "⚠️ Информация носит рекомендательный характер. Проконсультируйся с врачом.",
            S("disc", fontSize=7, textColor=MID, fontName="Helvetica-Oblique",
              alignment=TA_CENTER)
        ))
        return story

    # --- Запросы к БД -----------------------------------------------------

    async def _food_by_day(self, session, user_id, start, end) -> dict:
        result = await session.execute(
            select(
                FoodLog.meal_date,
                func.coalesce(func.sum(FoodLog.calories), 0).label("calories"),
                func.coalesce(func.sum(FoodLog.protein_g), 0).label("protein"),
                func.coalesce(func.sum(FoodLog.fat_g), 0).label("fat"),
                func.coalesce(func.sum(FoodLog.carbs_g), 0).label("carbs"),
            ).where(
                and_(FoodLog.user_id == user_id,
                     FoodLog.meal_date >= start,
                     FoodLog.meal_date <= end)
            ).group_by(FoodLog.meal_date)
        )
        return {
            row.meal_date: {
                "calories": row.calories, "protein": row.protein,
                "fat": row.fat, "carbs": row.carbs,
            }
            for row in result
        }

    async def _water_by_day(self, session, user_id, start, end) -> dict:
        result = await session.execute(
            select(
                WaterLog.log_date,
                func.coalesce(func.sum(WaterLog.amount_ml), 0).label("ml"),
            ).where(
                and_(WaterLog.user_id == user_id,
                     WaterLog.log_date >= start,
                     WaterLog.log_date <= end)
            ).group_by(WaterLog.log_date)
        )
        return {row.log_date: row.ml for row in result}

    async def _sleep_by_day(self, session, user_id, start, end) -> dict:
        result = await session.execute(
            select(SleepLog).where(
                and_(SleepLog.user_id == user_id,
                     SleepLog.log_date >= start,
                     SleepLog.log_date <= end)
            )
        )
        return {log.log_date: log for log in result.scalars()}

    async def _supplement_stats(self, session, user_id, start, end) -> list:
        sups_result = await session.execute(
            select(Supplement).where(
                Supplement.user_id == user_id,
                Supplement.is_active == True,
            )
        )
        sups = sups_result.scalars().all()
        if not sups:
            return []

        stats = []
        for sup in sups:
            logs_result = await session.execute(
                select(SupplementLog).where(
                    and_(SupplementLog.supplement_id == sup.id,
                         SupplementLog.log_date >= start,
                         SupplementLog.log_date <= end)
                )
            )
            logs = logs_result.scalars().all()
            taken   = sum(1 for l in logs if l.taken)
            skipped = sum(1 for l in logs if not l.taken)
            stats.append({"name": sup.name, "dose": sup.dose,
                          "taken": taken, "skipped": skipped})
        return stats


report_service = ReportService()
