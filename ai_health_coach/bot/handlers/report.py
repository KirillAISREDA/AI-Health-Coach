"""
Хэндлер PDF-отчёта.

Команда /report или кнопка в статистике.
Генерирует PDF за последние 7 дней и отправляет как документ.
"""

import logging
from datetime import date

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.report_service import report_service
from bot.services.ai_service import ai_service
from bot.services.user_service import user_service

logger = logging.getLogger(__name__)
router = Router()


def report_prompt_kb():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📄 Скачать PDF-отчёт", callback_data="report:generate"),
    )
    return builder.as_markup()


@router.message(F.text == "/report")
async def cmd_report(message: Message, db_user):
    if not db_user.onboarding_done:
        await message.answer("Сначала заполни анкету! /start")
        return
    await message.answer(
        "📊 <b>Еженедельный отчёт</b>\n\n"
        "Сгенерирую PDF с твоим прогрессом за последние 7 дней:\n"
        "├ 🥗 Питание по дням (КБЖУ)\n"
        "├ 💧 Водный баланс\n"
        "├ 😴 Качество сна\n"
        "├ 💊 Приём БАДов\n"
        "└ 🤖 Комментарий коуча",
        parse_mode="HTML",
        reply_markup=report_prompt_kb(),
    )


@router.callback_query(F.data == "report:generate")
async def cb_generate_report(
    call: CallbackQuery,
    bot: Bot,
    db_user,
    session: AsyncSession,
):
    thinking = await call.message.edit_text(
        "⏳ Генерирую отчёт...\n\n"
        "_Это займёт несколько секунд_",
        parse_mode="Markdown",
    )
    await call.answer()

    try:
        # AI-комментарий
        week_stats  = await user_service.get_week_stats(session, db_user.id)
        profile     = user_service.to_profile_dict(db_user)
        ai_comment  = await ai_service.generate_weekly_digest(
            user_id=db_user.id,
            week_stats=week_stats,
            user_profile=profile,
        )

        # Генерируем PDF
        pdf_bytes = await report_service.generate_weekly_pdf(
            session=session,
            user=db_user,
            ai_comment=ai_comment,
        )

        filename = f"report_{date.today().strftime('%Y_%m_%d')}.pdf"

        await thinking.delete()
        await bot.send_document(
            chat_id=call.from_user.id,
            document=BufferedInputFile(pdf_bytes, filename=filename),
            caption=(
                f"📄 <b>Твой отчёт за неделю готов!</b>\n\n"
                f"Файл: <code>{filename}</code>"
            ),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Report generation error for {db_user.id}: {e}", exc_info=True)
        await thinking.edit_text(
            "😕 Не удалось сгенерировать отчёт. Попробуй позже или напиши /report снова."
        )
