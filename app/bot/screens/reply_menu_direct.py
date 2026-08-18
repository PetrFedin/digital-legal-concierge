from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.client_case_view import load_client_case_view
from app.bot.context import BotContextService
from app.bot.keyboards import main_menu, one
from app.bot.screens import common, document_action_center, messages, my_case
from app.domain.cases.client_case_scope import latest_completed_case_for_user
from app.domain.consultations.consultation_intake import consultation_description_ready
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.documents.document_service import DocumentService
from app.domain.messages.message_service import MessageService
from app.domain.statuses.case_statuses import RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus

router = Router()


async def _active_message_case(message: Message, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return user, case


@router.message(lambda m: m.text in {"📁 Мое дело", "📁 Моё дело"})
async def direct_reply_my_case(message: Message, state: FSMContext, db):
    """Open the client cabinet immediately from the persistent reply keyboard."""

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
        markup = main_menu(
            case_exists,
            completed_case=completed_case,
            primary_action=primary_action,
        )

    await db.commit()
    await message.answer(text, reply_markup=markup)


@router.message(lambda m: m.text == "📄 Документы")
async def direct_reply_documents(message: Message, state: FSMContext, db):
    """Open the real document action center without an extra callback tap."""

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


@router.message(lambda m: m.text == "💬 Переписка")
async def direct_reply_message_history(message: Message, state: FSMContext, db):
    """Show the first real dialog page and mark only displayed team replies read."""

    if await common._guard_message_draft(message, state):
        return
    await state.clear()
    user, case = await _active_message_case(message, db)
    read_only = False
    if case is None:
        case = await latest_completed_case_for_user(db, user_id=user.id)
        read_only = case is not None
    if case is None:
        await db.commit()
        await message.answer(
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
            0,
            read_only=read_only,
        )
        page_messages, _, _ = messages._history_slice(dialog, page)
        visible_team_ids = tuple(
            int(item.id)
            for item in page_messages
            if item.sender_type == "lawyer"
        )
        # Do not touch the ORM Case after rollback: AsyncSession may expire it.
        await db.rollback()
    except Exception:
        await db.rollback()
        await message.answer(
            "Не удалось загрузить переписку. Данные не изменены.",
            reply_markup=one(
                ("🔄 Повторить", "message_history:0"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await message.answer(
        text,
        reply_markup=messages._history_keyboard(
            page,
            total_pages,
            read_only=read_only,
        ),
    )
    if visible_team_ids and not read_only:
        try:
            await service.mark_lawyer_messages_read(
                case_id,
                message_ids=visible_team_ids,
            )
            await db.commit()
        except Exception:
            await db.rollback()


@router.message(lambda m: m.text == "✉️ Новый вопрос")
async def direct_reply_new_question(message: Message, state: FSMContext, db):
    """Start a case-bound draft immediately when the active reply menu asks for it."""

    if await common._guard_message_draft(message, state):
        return
    user, case = await _active_message_case(message, db)
    if case is None:
        completed = await latest_completed_case_for_user(db, user_id=user.id)
        await state.clear()
        await db.commit()
        if completed is not None:
            await message.answer(
                "🔒 Завершённое обращение не принимает новые сообщения.\n\n"
                "Архив остаётся без изменений. Если вопрос новый, создайте отдельное обращение явно.",
                reply_markup=one(
                    ("💬 Архив переписки", "message_history"),
                    ("📁 Итог обращения", "my_case_open"),
                    ("🆕 Создать новое обращение", "message_new_request"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return
        await message.answer(
            "✉️ Активного дела сейчас нет.\n\n"
            "Новый вопрос не создаст обращение автоматически. Подтвердите новый запрос отдельным действием.",
            reply_markup=one(
                ("🆕 Создать новое обращение", "message_new_request"),
                ("🧮 Рассчитать неустойку", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await state.clear()
    await state.update_data(
        case_id=int(case.id),
        case_number=str(case.case_number),
        new_request_confirmed=False,
    )
    await state.set_state(messages.MessageStates.choosing_category)
    data = await state.get_data()
    await db.commit()
    await message.answer(
        messages._category_prompt(data),
        reply_markup=one(*messages._category_buttons()),
    )


@router.message(lambda m: m.text == "💬 Связаться с юристом")
async def direct_reply_contact_lawyer(message: Message, state: FSMContext, db):
    """Open route-aware legal help directly without creating a consultation yet."""

    if await common._guard_message_draft(message, state):
        return
    await state.clear()
    _user, case = await _active_message_case(message, db)

    if case is not None and str(case.route or "") != RouteCode.M2.value:
        await db.commit()
        await message.answer(
            "💬 Связаться с юридической командой\n\n"
            "У вас уже есть активное дело. Напишите по нему или откройте переписку — "
            "отдельная консультация не заменит и не скроет текущее дело.",
            reply_markup=one(
                ("✉️ Написать по текущему делу", "message_create"),
                ("🗂 Открыть переписку", "message_history"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if case is not None:
        consultation = await ConsultationService(db).get_current_for_case(case.id)
        if consultation and consultation.status == ConsultationStatus.BOOKED:
            primary = (
                "👨‍⚖ Открыть подтверждённую запись",
                "consultation_booked_open",
            )
        elif consultation and consultation_description_ready(consultation):
            primary = ("📅 Продолжить: выбрать время", "consult_booking_start")
        else:
            primary = ("📝 Продолжить: описать вопрос", "consult_subject_start")
        await db.commit()
        await message.answer(
            "💬 Юридическая консультация\n\n"
            "Продолжите сохранённый этап консультации либо напишите команде по обращению.",
            reply_markup=one(
                primary,
                ("✉️ Написать сообщение", "message_create"),
                ("🗂 Открыть переписку", "message_history"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    # First legal-help entry is presentation-only. M2 case/consultation creation
    # still happens behind the explicit consult_subject_start action, where the
    # serialized one-active-case invariant and ActiveCaseRouteConflict apply.
    await db.commit()
    await message.answer(
        "💬 Юридическая консультация\n\n"
        "Сначала опишите ситуацию и конкретный вопрос. После этого можно "
        "добавить документы и выбрать свободное время.",
        reply_markup=one(
            ("▶️ Начать: описать вопрос", "consult_subject_start"),
            ("🧮 Рассчитать неустойку", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
