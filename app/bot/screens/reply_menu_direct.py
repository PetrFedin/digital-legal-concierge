from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.client_case_view import load_client_case_view, route_label
from app.bot.context import BotContextService
from app.bot.keyboards import main_menu, one
from app.bot.screens import common, document_action_center, messages, my_case
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.client_case_scope import latest_completed_case_for_user
from app.domain.consultations.consultation_intake import consultation_description_ready
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.documents.document_service import DocumentService
from app.domain.messages.message_service import MessageService
from app.domain.statuses.case_statuses import RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus

router = Router()


async def _message_context(message: Message, db):
    """Resolve a safe Case for a persistent Telegram reply-menu action.

    A valid selected active Case wins. If selection has become terminal/missing,
    a single remaining active Case is unambiguous and may be used directly. With
    two or more active Cases there is deliberately no implicit newest-Case
    fallback: the client must choose the matter before documents/messages/legal
    help can act in a Case context.
    """

    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    selected = await ctx.case_service.get_selected_case_for_user(
        int(user.id),
        include_terminal=False,
    )
    if selected is not None:
        return ctx, user, selected

    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    case = active_cases[0] if len(active_cases) == 1 else None
    return ctx, user, case


async def _active_message_case(message: Message, db):
    _ctx, user, case = await _message_context(message, db)
    return user, case


def _matter_label(case) -> str:
    status = get_client_visible_status(case.status)
    service = route_label(case.route)
    return f"{case.case_number} · {service} · {status}"


async def _matter_selector_text(user, db) -> tuple[str, list[tuple[str, str]]]:
    ctx = BotContextService(db)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    buttons: list[tuple[str, str]] = []
    lines = [
        "📁 МОИ ОБРАЩЕНИЯ",
        "",
        "У вас несколько активных обращений. Выберите нужное — документы, переписка, оплаты и действия дальше будут относиться именно к нему.",
        "",
    ]
    for item in active_cases:
        lines.append(f"• {_matter_label(item)}")
        buttons.append(
            (
                f"📁 {item.case_number} · {route_label(item.route)}",
                f"my_case_select:v2:{int(item.id)}",
            )
        )
    buttons.extend(
        [
            ("🧮 Новый расчёт / новое обращение", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    return "\n".join(lines), buttons


async def _show_selector_if_ambiguous(message: Message, *, ctx, user, case, db) -> bool:
    if case is not None:
        return False
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    if len(active_cases) <= 1:
        return False
    text, buttons = await _matter_selector_text(user, db)
    await db.commit()
    await message.answer(text, reply_markup=one(*buttons))
    return True


@router.message(lambda m: m.text == "🧮 Рассчитать неустойку")
async def direct_reply_calculator(message: Message, state: FSMContext, db):
    """Calculator is always available and creates a separate matter explicitly."""

    if await common._guard_message_draft(message, state):
        return
    await state.clear()
    ctx, user, _selected = await _message_context(message, db)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    await db.commit()
    if active_cases:
        await message.answer(
            "🧮 НОВЫЙ РАСЧЁТ\n\n"
            "Расчёт доступен независимо от уже открытых дел. Если вы продолжите, будет создано отдельное обращение; существующие M1/M2 дела, документы и статусы не изменятся.\n\n"
            f"Сейчас активных обращений: {len(active_cases)}.",
            reply_markup=one(
                ("▶️ Начать новый расчёт", "calc_start"),
                ("📁 Выбрать текущее дело", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await message.answer(
        "🧮 ПРЕДВАРИТЕЛЬНЫЙ РАСЧЁТ\n\n"
        "Ответьте на несколько вопросов о ДДУ. Расчёт предварительный и не является юридическим заключением.",
        reply_markup=one(
            ("▶️ Начать расчёт", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(lambda m: m.text in {"📁 Мое дело", "📁 Моё дело"})
async def direct_reply_my_case(message: Message, state: FSMContext, db):
    """Open one Case directly or show a deterministic selector for several."""

    if await common._guard_message_draft(message, state):
        return
    await state.clear()

    ctx, user, case = await _message_context(message, db)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    if len(active_cases) > 1:
        text, buttons = await _matter_selector_text(user, db)
        await db.commit()
        await message.answer(text, reply_markup=one(*buttons))
        return

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


@router.callback_query(lambda c: c.data == "my_cases_open")
async def open_case_selector(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    if len(active_cases) <= 1:
        await db.commit()
        await my_case._render_case(callback, db)
        return
    text, buttons = await _matter_selector_text(user, db)
    await db.commit()
    await callback.message.edit_text(text, reply_markup=one(*buttons))


@router.callback_query(
    lambda c: bool(c.data) and c.data.startswith("my_case_select:v2:")
)
async def select_client_case(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case_id = int(str(callback.data).split(":", 2)[2])
        selected = await ctx.case_service.select_case_for_user(
            user_id=int(user.id),
            case_id=case_id,
        )
        case_number = str(selected.case_number)
        await db.commit()
    except Exception:
        await db.rollback()
        await callback.message.edit_text(
            "Не удалось выбрать это обращение. Оно могло быть закрыто, удалено или относиться к другой учётной записи.",
            reply_markup=one(
                ("📁 Обновить список обращений", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await my_case._render_case(
        callback,
        db,
        notice=f"Выбрано обращение {case_number}.",
    )


@router.message(lambda m: m.text == "📄 Документы")
async def direct_reply_documents(message: Message, state: FSMContext, db):
    """Open the selected Case document center without cross-Case fallback."""

    if await common._guard_message_draft(message, state):
        return
    await document_action_center._clear_document_upload_state(state)
    ctx, user, case = await _message_context(message, db)
    if await _show_selector_if_ambiguous(
        message,
        ctx=ctx,
        user=user,
        case=case,
        db=db,
    ):
        return
    if case is None:
        await db.commit()
        await message.answer(
            "📄 ДОКУМЕНТЫ\n\n"
            "Выбранного активного обращения сейчас нет. Файл не будет автоматически привязан к другому делу.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🧮 Новое обращение", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    case_number = str(case.case_number)
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
        "📄 ДОКУМЕНТЫ\n"
        f"Обращение № {case_number}\n\n"
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
    """Compatibility for historical keyboards; canonical menu uses Contact Lawyer."""

    if await common._guard_message_draft(message, state):
        return
    await state.clear()
    ctx, user, case = await _message_context(message, db)
    if await _show_selector_if_ambiguous(
        message,
        ctx=ctx,
        user=user,
        case=case,
        db=db,
    ):
        return

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
    """Compatibility for historical keyboards; starts a draft for selected Case."""

    if await common._guard_message_draft(message, state):
        return
    ctx, user, case = await _message_context(message, db)
    if await _show_selector_if_ambiguous(
        message,
        ctx=ctx,
        user=user,
        case=case,
        db=db,
    ):
        return

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
            "✉️ Выбранного активного дела сейчас нет.\n\n"
            "Новый вопрос не будет автоматически записан в другое обращение. Подтвердите новый запрос отдельным действием.",
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
    """Open legal help for the explicitly selected Case, without route mixing."""

    if await common._guard_message_draft(message, state):
        return
    await state.clear()
    ctx, user, case = await _message_context(message, db)
    if await _show_selector_if_ambiguous(
        message,
        ctx=ctx,
        user=user,
        case=case,
        db=db,
    ):
        return

    if case is not None and str(case.route or "") != RouteCode.M2.value:
        case_number = str(case.case_number)
        await db.commit()
        await message.answer(
            "💬 СВЯЗАТЬСЯ С ЮРИСТОМ\n"
            f"Обращение № {case_number}\n\n"
            "Для выбранного M1-дела связь с юристом идёт через переписку этого обращения. Консультационный маршрут не подменяет и не меняет M1.",
            reply_markup=one(
                ("✉️ Написать по выбранному делу", "message_create"),
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
        case_number = str(case.case_number)
        await db.commit()
        await message.answer(
            "💬 ЮРИДИЧЕСКАЯ КОНСУЛЬТАЦИЯ\n"
            f"Обращение № {case_number}\n\n"
            "Продолжите сохранённый этап консультации либо напишите команде по этому обращению.",
            reply_markup=one(
                primary,
                ("✉️ Написать сообщение", "message_create"),
                ("🗂 Открыть переписку", "message_history"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    # First legal-help entry is presentation-only. The explicit
    # consult_subject_start callback creates M2 through source-operation
    # idempotency, so Telegram redelivery cannot create duplicate Cases.
    await db.commit()
    await message.answer(
        "💬 ЮРИДИЧЕСКАЯ КОНСУЛЬТАЦИЯ\n\n"
        "Сначала опишите ситуацию и конкретный вопрос. После этого можно добавить документы и выбрать свободное время.",
        reply_markup=one(
            ("▶️ Начать: описать вопрос", "consult_subject_start"),
            ("🧮 Рассчитать неустойку", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
