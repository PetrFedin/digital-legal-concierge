from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.client_case_view import load_client_case_view
from app.bot.context import BotContextService
from app.bot.keyboards import main_menu, one
from app.bot.screens import common, my_case

router = Router()


@router.message(lambda m: m.text in {"📁 Мое дело", "📁 Моё дело"})
async def direct_reply_my_case(message: Message, state: FSMContext, db):
    """Open the client cabinet immediately from the persistent reply keyboard.

    The old reply-menu handler rendered a trampoline with another `Моё дело`
    callback, forcing a second tap. Reuse the existing shared case view and the
    same My Case action-button builder instead of duplicating legal/payment
    decision logic or fabricating a CallbackQuery.
    """

    if await common._guard_message_draft(message, state):
        return
    await state.clear()

    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)

    text, case_exists, completed_case, primary_action = await common._home_text(
        db,
        message,
    )
    if text.startswith("🏠 Главная"):
        text = text.replace("🏠 Главная", "📁 МОЁ ДЕЛО", 1)
    elif text.startswith("🏠 Добро пожаловать"):
        text = text.replace("🏠 Добро пожаловать", "📁 МОЁ ДЕЛО", 1)

    if case is not None:
        view = await load_client_case_view(db, case)
        markup = one(*my_case._case_buttons(view))
    else:
        # Completed/no-case presentation already comes from the shared Home
        # presenter. Its primary action points to the archived result when one
        # exists and otherwise offers calculation/legal-help entry.
        markup = main_menu(
            case_exists,
            completed_case=completed_case,
            primary_action=primary_action,
        )

    await db.commit()
    await message.answer(text, reply_markup=markup)


__all__ = ["router"]
