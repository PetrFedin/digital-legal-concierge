from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.client_case_view import load_client_case_view
from app.bot.context import BotContextService
from app.bot.keyboards import main_menu, one
from app.bot.screens import common, document_action_center, my_case
from app.domain.documents.document_service import DocumentService

router = Router()


async def _active_message_case(message: Message, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return user, case


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

    _user, case = await _active_message_case(message, db)
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


@router.message(lambda m: m.text == "📄 Документы")
async def direct_reply_documents(message: Message, state: FSMContext, db):
    """Open the real document action center without an extra callback tap.

    Mutation eligibility stays in document_action_center._next_action, including
    the runtime v2 case-bound handoff callbacks installed by client_wording_patch.
    This handler only adapts that same presentation to Message.answer().
    """

    if await common._guard_message_draft(message, state):
        return
    await document_action_center._clear_document_upload_state(state)
    _user, case = await _active_message_case(message, db)
    if case is None:
        await db.commit()
        await message.answer(
            "📄 ДОКУМЕНТЫ\n\n"
            "Активное дело уже завершено или отсутствует. Старая кнопка нижнего меню "
            "не создаёт новое обращение и не загружает файл в другой кейс.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🧮 Новое обращение", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    all_documents = await DocumentService(db).list_case_documents(case.id)
    documents = document_action_center._active(all_documents)
    archived_count = len(all_documents) - len(documents)
    counts = document_action_center._counts(documents)
    next_step, primary = document_action_center._next_action(case, documents)

    summary = (
        f"Актуальных: {len(documents)} · требуется: {counts['required']} · "
        f"к передаче: {counts['new']} · на проверке: {counts['review']} · "
        f"принято: {counts['approved']} · требуют замены: {counts['replacement']}"
    )
    preview = "\n".join(
        document_action_center._document_line(item)
        for item in documents[:5]
    )
    if not preview:
        preview = "• Документов пока нет."
    elif len(documents) > 5:
        preview += f"\n• Ещё актуальных документов: {len(documents) - 5}."

    buttons = list(primary)
    primary_callbacks = {callback_data for _, callback_data in primary}
    if documents:
        buttons.append(("📋 Все актуальные документы", "documents_list_open"))
    if (
        "documents_upload_open" not in primary_callbacks
        and not counts["replacement"]
    ):
        buttons.append(("➕ Добавить документ", "documents_upload_open"))
    if archived_count:
        buttons.append(
            (f"🕘 История версий ({archived_count})", "documents_history_open")
        )
    buttons.extend(
        [
            ("✉️ Задать вопрос по документам", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )

    await db.commit()
    await message.answer(
        "📄 ДОКУМЕНТЫ\n\n"
        "СЕЙЧАС\n"
        f"{summary}\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        f"{next_step}\n\n"
        "АКТУАЛЬНЫЕ ДОКУМЕНТЫ\n"
        f"{preview}",
        reply_markup=one(*buttons),
    )


__all__ = ["router"]
