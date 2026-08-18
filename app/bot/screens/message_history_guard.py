from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import messages
from app.domain.cases.client_case_scope import latest_completed_case_for_user
from app.domain.messages.message_service import MessageService


async def present_message_history(
    callback: CallbackQuery,
    db,
    state: FSMContext,
) -> None:
    """Render callback message history without touching an expired Case object.

    AsyncSession.rollback() intentionally releases the read transaction before
    Telegram network I/O. SQLAlchemy may expire ORM instances on rollback, so
    every scalar needed afterwards is captured first. This guard is used by the
    early navigation router for both the first page and pagination callbacks.
    """

    if await messages._guard_existing_draft(callback, state):
        return

    requested_page = messages._history_page_from_callback(callback.data)
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    read_only = False
    if case is None:
        case = await latest_completed_case_for_user(db, user_id=user.id)
        read_only = case is not None
    if case is None:
        await messages._safe_edit(
            callback,
            "💬 История переписки появится после создания обращения.\n\n"
            "Начните с предварительного расчёта или откройте связь с юридической командой.",
            reply_markup=one(
                ("🧮 Рассчитать неустойку", "calc_start"),
                ("💬 Связаться с юристом", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    case_id = int(case.id)
    service = MessageService(db)
    try:
        dialog = await service.list_case_messages(case_id, limit=100)
        text, page, total_pages = messages._format_dialog(
            dialog,
            requested_page,
            read_only=read_only,
        )
        page_messages, _, _ = messages._history_slice(dialog, page)
        visible_team_ids = tuple(
            int(item.id)
            for item in page_messages
            if item.sender_type == "lawyer"
        )
        # Release the read transaction before Telegram network I/O. Do not
        # access `case` after this point; rollback may expire ORM attributes.
        await db.rollback()
    except Exception:
        await db.rollback()
        await messages._safe_edit(
            callback,
            "Не удалось загрузить переписку. Данные не изменены.",
            reply_markup=one(
                ("🔄 Повторить", f"message_history:{requested_page}"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    markup = messages._history_keyboard(page, total_pages, read_only=read_only)
    try:
        changed = await messages._safe_edit(callback, text, reply_markup=markup)
        if changed:
            await callback.answer()
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup)
        await callback.answer("Переписка открыта новым сообщением.")

    if visible_team_ids and not read_only:
        try:
            await service.mark_lawyer_messages_read(
                case_id,
                message_ids=visible_team_ids,
            )
            await db.commit()
        except Exception:
            await db.rollback()


__all__ = ["present_message_history"]
