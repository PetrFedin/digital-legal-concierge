from __future__ import annotations

from math import ceil

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.case_callback_scope import (
    bound_case_callback,
    callback_matches_action,
    resolve_case_callback_scope,
)
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.client_case_scope import (
    CLIENT_COMPLETED_CASE_STATUSES,
    latest_completed_case_for_user,
)
from app.domain.messages.message_service import MessageService
from app.domain.notifications.immediate_delivery import deliver_selected_notifications
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.mode import payments_disabled
from app.models.case import Case
from app.presentation_time import format_business_datetime

router = Router()

HISTORY_PAGE_SIZE = 5
HISTORY_ITEM_TEXT_LIMIT = 560
HISTORY_TEXT_LIMIT = 3800
NOTIFICATION_TEXT_LIMIT = 3000
DRAFT_PREVIEW_LIMIT = 2800

MESSAGE_CATEGORIES = {
    "msg_cat_documents": "Документы",
    "msg_cat_deadline": "Сроки и заседание",
    "msg_cat_payment": "Оплата и возврат",
    "msg_cat_case": "Ход дела",
    "msg_cat_other": "Другой вопрос",
}

URGENCY_LEVELS = {
    "msg_urgency_normal": "Обычный",
    "msg_urgency_soon": "Нужен ответ сегодня",
    "msg_urgency_critical": "Критично: срок менее 24 часов",
}
COMPLETED_STATUS_VALUES = {str(value) for value in CLIENT_COMPLETED_CASE_STATUSES}


class MessageStates(StatesGroup):
    choosing_category = State()
    choosing_urgency = State()
    waiting_message = State()
    confirming_message = State()


class MessageTargetChanged(RuntimeError):
    """The draft target no longer matches the active client case."""


def _truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _category_title(callback_data: str) -> str:
    if callback_data == "msg_cat_payment" and payments_disabled():
        return "Стоимость и условия"
    return MESSAGE_CATEGORIES[callback_data]


def _category_buttons() -> list[tuple[str, str]]:
    payment_label = (
        "💼 Стоимость и условия"
        if payments_disabled()
        else "💳 Оплата и возврат"
    )
    return [
        ("📄 Документы", "msg_cat_documents"),
        ("⏰ Сроки и заседание", "msg_cat_deadline"),
        (payment_label, "msg_cat_payment"),
        ("📁 Ход дела", "msg_cat_case"),
        ("❓ Другой вопрос", "msg_cat_other"),
        ("Отменить действие", "nav_cancel"),
    ]


def _draft_case_context(data: dict[str, object]) -> str:
    case_number = str(data.get("case_number") or "").strip()
    return f"Обращение № {case_number}" if case_number else "Новое обращение"


def _category_prompt(data: dict[str, object]) -> str:
    return (
        "✉️ НОВЫЙ ВОПРОС\n"
        f"{_draft_case_context(data)}\n\n"
        "СЕЙЧАС\n"
        "Черновик открыт, но ничего ещё не отправлено.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Выберите тему вопроса:"
    )


def _urgency_prompt(data: dict[str, object]) -> str:
    category = str(data.get("category") or "Не выбрана")
    return (
        "⏱ СРОЧНОСТЬ ВОПРОСА\n"
        f"{_draft_case_context(data)}\n"
        f"Тема: {category}\n\n"
        "СЕЙЧАС\n"
        "Черновик сохранён локально в текущем диалоге и не отправлен.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Укажите, когда нужен ответ."
    )


def _message_prompt(data: dict[str, object], *, editing: bool = False) -> str:
    category = str(data.get("category") or "Другой вопрос")
    urgency = str(data.get("urgency") or "Обычный")
    edit_note = (
        "Текущий текст сохранён до тех пор, пока вы не отправите новую редакцию.\n"
        if editing and data.get("draft_text")
        else ""
    )
    return (
        "📝 ТЕКСТ ВОПРОСА\n"
        f"{_draft_case_context(data)}\n"
        f"Тема: {category}\n"
        f"Срочность: {urgency}\n\n"
        "СЕЙЧАС\n"
        f"{edit_note}Ничего не отправлено.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Опишите, что произошло, какой результат вы ожидаете, важные даты и документы. "
        "Не отправляйте пароли, коды из SMS и банковские данные."
    )


def _draft_review_text(data: dict[str, object]) -> str:
    category = str(data.get("category") or "Другой вопрос")
    urgency = str(data.get("urgency") or "Обычный")
    draft = str(data.get("draft_text") or "").strip()
    preview = _truncate(draft, DRAFT_PREVIEW_LIMIT)
    shortened_note = (
        "\n\nПредпросмотр сокращён. При подтверждении будет отправлен весь сохранённый текст."
        if preview != draft
        else ""
    )
    return (
        "✅ ПРОВЕРКА ПЕРЕД ОТПРАВКОЙ\n"
        f"{_draft_case_context(data)}\n"
        f"Тема: {category}\n"
        f"Срочность: {urgency}\n\n"
        "СЕЙЧАС\n"
        "Черновик сохранён и ещё не отправлен.\n\n"
        f"Текст:\n{preview}"
        f"{shortened_note}\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Проверьте контекст и нажмите «Отправить вопрос». До этого команда ничего не получит."
    )


def _review_markup():
    return one(
        ("✅ Отправить вопрос", "message_submit"),
        ("✏️ Изменить текст", "message_edit_text"),
        ("⏱ Изменить срочность", "message_back_urgency"),
        ("🧭 Изменить тему", "message_back_category"),
        ("✖️ Отменить черновик", "message_discard_confirm"),
    )


def _history_page_from_callback(callback_data: str | None) -> int:
    if not callback_data or ":" not in callback_data:
        return 0
    try:
        return max(0, int(callback_data.rsplit(":", 1)[1]))
    except (TypeError, ValueError):
        return 0


def _history_slice(messages, requested_page: int):
    total_pages = max(1, ceil(len(messages) / HISTORY_PAGE_SIZE))
    page = min(max(0, requested_page), total_pages - 1)
    if not messages:
        return [], page, total_pages
    end = len(messages) - page * HISTORY_PAGE_SIZE
    start = max(0, end - HISTORY_PAGE_SIZE)
    return list(messages[start:end]), page, total_pages


def _format_dialog(
    messages,
    requested_page: int = 0,
    *,
    read_only: bool = False,
    case_number: str | None = None,
) -> tuple[str, int, int]:
    page_messages, page, total_pages = _history_slice(messages, requested_page)
    heading = "💬 ПЕРЕПИСКА · АРХИВ" if read_only else "💬 ПЕРЕПИСКА ПО ДЕЛУ"
    context = f"Обращение № {case_number}" if case_number else "Обращение"
    if not page_messages:
        detail = (
            "Сообщений в архиве нет."
            if read_only
            else "Сообщений пока нет. Если нужен ответ команды, создайте новый вопрос."
        )
        return (
            f"{heading}\n{context}\n\nСЕЙЧАС\n{detail}",
            page,
            total_pages,
        )

    lines = [
        heading,
        context,
        "",
        "СЕЙЧАС",
        f"Страница {page + 1} из {total_pages}. Первая страница — самые новые сообщения.",
    ]
    for item in page_messages:
        author = "Вы" if item.sender_type == "client" else "Команда"
        created_at = (
            format_business_datetime(item.created_at)
            if item.created_at
            else "время не указано"
        )
        body = _truncate(item.text, HISTORY_ITEM_TEXT_LIMIT)
        lines.append(f"{author} · {created_at}\n{body}")
    return _truncate("\n\n".join(lines), HISTORY_TEXT_LIMIT), page, total_pages


def _history_keyboard(
    page: int,
    total_pages: int,
    *,
    read_only: bool = False,
    case_id: int | None = None,
):
    buttons: list[tuple[str, str]] = []
    if page < total_pages - 1:
        buttons.append(("⬅️ Более ранние", f"message_history:{page + 1}"))
    if page > 0:
        buttons.append(("Более новые ➡️", f"message_history:{page - 1}"))
    if not read_only:
        create_callback = (
            bound_case_callback("message_create", case_id)
            if case_id is not None
            else "message_create"
        )
        buttons.append(("✉️ Написать сообщение", create_callback))
    buttons.append(("🔄 Обновить", f"message_history:{page}"))
    if read_only:
        buttons.append(("🕘 История дела", "case_history_open"))
    buttons.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    return one(*buttons)


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> bool:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            await callback.answer("Переписка уже актуальна.")
            return False
        raise


async def _replace_or_send(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=reply_markup)


def _delivery_status_text(
    delivery: dict[str, object],
    *,
    created: bool,
) -> str:
    if not created:
        return (
            "Статус: этот Telegram-вопрос уже был зарегистрирован; "
            "повторная запись не создана."
        )
    status = str(delivery.get("status") or "queued")
    if status == "delivered":
        return "Статус: команда уведомлена в Telegram, вопрос виден в кабинете."
    if status == "failed":
        return (
            "Статус: вопрос сохранён и виден в кабинете. Сбой внутреннего "
            "Telegram-уведомления передан администратору."
        )
    if status == "already_processing":
        return (
            "Статус: вопрос сохранён; внутреннее уведомление уже обрабатывается."
        )
    return (
        "Статус: вопрос сохранён и виден в кабинете; внутреннее уведомление "
        "поставлено на повторную доставку."
    )


async def _guard_existing_draft(callback: CallbackQuery, state: FSMContext) -> bool:
    data = await state.get_data()
    if not str(data.get("draft_text") or "").strip():
        return False
    await state.set_state(MessageStates.confirming_message)
    await _replace_or_send(
        callback,
        "📝 У вас уже есть неотправленный черновик. Он не удалён.\n\n"
        + _draft_review_text(data),
        reply_markup=_review_markup(),
    )
    await callback.answer("Сначала завершите или удалите черновик.")
    return True


async def _start_message_draft(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    case: Case | None,
) -> None:
    await state.clear()
    await state.update_data(
        case_id=int(case.id) if case else None,
        case_number=str(case.case_number) if case else None,
        new_request_confirmed=case is None,
    )
    await state.set_state(MessageStates.choosing_category)
    data = await state.get_data()
    await callback.message.edit_text(
        _category_prompt(data),
        reply_markup=one(*_category_buttons()),
    )


async def _locked_message_target(db, ctx, user, data: dict[str, object]) -> Case:
    """Resolve the exact case captured when the draft started, under a row lock."""

    raw_case_id = data.get("case_id")
    if raw_case_id is not None:
        try:
            case_id = int(raw_case_id)
        except (TypeError, ValueError) as error:
            raise MessageTargetChanged(
                "Исходное дело черновика больше не определяется однозначно."
            ) from error
        case = (
            await db.execute(
                select(Case)
                .where(Case.id == case_id, Case.client_id == user.id)
                .with_for_update()
            )
        ).scalars().first()
        if case is None or str(case.status) in COMPLETED_STATUS_VALUES:
            raise MessageTargetChanged(
                "Исходное дело уже завершено или недоступно для новых сообщений."
            )
        active = await ctx.case_service.get_active_case_for_user(user.id)
        if active is None or int(active.id) != case_id:
            raise MessageTargetChanged(
                "Активное дело изменилось после создания черновика."
            )
        return case

    if not bool(data.get("new_request_confirmed")):
        raise MessageTargetChanged(
            "Создание нового обращения не было подтверждено."
        )

    active = await ctx.case_service.get_active_case_for_user(user.id)
    if active is not None:
        raise MessageTargetChanged(
            "Пока вы готовили новое обращение, появилось активное дело. "
            "Черновик не прикреплён к нему автоматически."
        )
    return await ctx.get_or_create_active_case_for_user(user)


@router.callback_query(
    lambda c: bool(c.data)
    and (c.data == "message_history" or c.data.startswith("message_history:"))
)
async def message_history(callback: CallbackQuery, db, state: FSMContext):
    if await _guard_existing_draft(callback, state):
        return
    requested_page = _history_page_from_callback(callback.data)
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    read_only = False
    if not case:
        case = await latest_completed_case_for_user(db, user_id=user.id)
        read_only = case is not None
    if not case:
        await _safe_edit(
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
    case_number = str(case.case_number)
    service = MessageService(db)
    try:
        messages = await service.list_case_messages(case_id, limit=100)
        text, page, total_pages = _format_dialog(
            messages,
            requested_page,
            read_only=read_only,
            case_number=case_number,
        )
        page_messages, _, _ = _history_slice(messages, page)
        visible_team_ids = tuple(
            int(item.id)
            for item in page_messages
            if item.sender_type == "lawyer"
        )
        await db.rollback()
    except Exception:
        await db.rollback()
        await _safe_edit(
            callback,
            "Не удалось загрузить переписку. Данные не изменены.",
            reply_markup=one(
                ("🔄 Повторить", f"message_history:{requested_page}"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    markup = _history_keyboard(
        page,
        total_pages,
        read_only=read_only,
        case_id=None if read_only else case_id,
    )
    try:
        changed = await _safe_edit(callback, text, reply_markup=markup)
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


@router.callback_query(lambda c: callback_matches_action(c.data, "message_create"))
async def message_create(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    if await _guard_existing_draft(callback, state):
        return
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        await _start_message_draft(callback, state, case=case)
        return

    await state.clear()
    completed = await latest_completed_case_for_user(db, user_id=user.id)
    if completed:
        await callback.message.edit_text(
            "🔒 Это старое действие от уже завершённого обращения.\n\n"
            f"Дело {completed.case_number} не принимает новые сообщения. "
            "Архив останется без изменений. Если вопрос новый, создайте отдельное обращение явно.",
            reply_markup=one(
                ("💬 Архив переписки", "message_history"),
                ("📁 Итог обращения", "my_case_open"),
                ("🆕 Создать новое обращение", "message_new_request"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        "✉️ Активного дела сейчас нет.\n\n"
        "Чтобы старое сообщение или случайная кнопка не создали новое дело автоматически, "
        "подтвердите новый запрос отдельным действием.",
        reply_markup=one(
            ("🆕 Создать новое обращение", "message_new_request"),
            ("🧮 Рассчитать неустойку", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "message_new_request")
async def message_new_request(callback: CallbackQuery, state: FSMContext, db):
    if await _guard_existing_draft(callback, state):
        return
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active = await ctx.case_service.get_active_case_for_user(user.id)
    if active:
        await callback.message.edit_text(
            "📁 Пока вы открывали новый запрос, появилось активное дело. "
            "Новое обращение не создано. Выберите, хотите ли написать по текущему делу.",
            reply_markup=one(
                (
                    "✉️ Написать по текущему делу",
                    bound_case_callback("message_create", int(active.id)),
                ),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await _start_message_draft(callback, state, case=None)


@router.callback_query(lambda c: c.data == "message_back_category")
async def message_back_category(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    if not data:
        await message_create(callback, state, db)
        return
    await state.set_state(MessageStates.choosing_category)
    await callback.message.edit_text(
        _category_prompt(data),
        reply_markup=one(*_category_buttons()),
    )
    await callback.answer()


@router.callback_query(
    MessageStates.choosing_category,
    lambda c: c.data in MESSAGE_CATEGORIES,
)
async def message_category(callback: CallbackQuery, state: FSMContext):
    await state.update_data(category=_category_title(callback.data))
    data = await state.get_data()
    if data.get("draft_text") and data.get("urgency"):
        await state.set_state(MessageStates.confirming_message)
        await callback.message.edit_text(
            _draft_review_text(data),
            reply_markup=_review_markup(),
        )
        await callback.answer("Тема обновлена.")
        return

    await state.set_state(MessageStates.choosing_urgency)
    await callback.message.edit_text(
        _urgency_prompt(data),
        reply_markup=one(
            ("Обычный вопрос", "msg_urgency_normal"),
            ("Нужен ответ сегодня", "msg_urgency_soon"),
            ("Критично: срок менее 24 часов", "msg_urgency_critical"),
            ("⬅️ Назад к теме", "message_back_category"),
            ("Отменить действие", "nav_cancel"),
        ),
    )


@router.callback_query(lambda c: c.data == "message_back_urgency")
async def message_back_urgency(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    if not data.get("category"):
        await message_create(callback, state, db)
        return
    await state.set_state(MessageStates.choosing_urgency)
    await callback.message.edit_text(
        _urgency_prompt(data),
        reply_markup=one(
            ("Обычный вопрос", "msg_urgency_normal"),
            ("Нужен ответ сегодня", "msg_urgency_soon"),
            ("Критично: срок менее 24 часов", "msg_urgency_critical"),
            ("⬅️ Назад к теме", "message_back_category"),
            ("Отменить действие", "nav_cancel"),
        ),
    )
    await callback.answer()


@router.callback_query(
    MessageStates.choosing_urgency,
    lambda c: c.data in URGENCY_LEVELS,
)
async def message_urgency(callback: CallbackQuery, state: FSMContext):
    await state.update_data(urgency=URGENCY_LEVELS[callback.data])
    data = await state.get_data()
    if data.get("draft_text"):
        await state.set_state(MessageStates.confirming_message)
        await callback.message.edit_text(
            _draft_review_text(data),
            reply_markup=_review_markup(),
        )
        await callback.answer("Срочность обновлена.")
        return

    await state.set_state(MessageStates.waiting_message)
    await callback.message.edit_text(
        _message_prompt(data),
        reply_markup=one(
            ("⬅️ Назад к срочности", "message_back_urgency"),
            ("Отменить действие", "nav_cancel"),
        ),
    )


@router.callback_query(lambda c: c.data == "message_edit_text")
async def message_edit_text(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    if not data.get("category") or not data.get("urgency"):
        await message_create(callback, state, db)
        return
    await state.set_state(MessageStates.waiting_message)
    await callback.message.edit_text(
        _message_prompt(data, editing=True),
        reply_markup=one(
            ("⬅️ Назад к срочности", "message_back_urgency"),
            ("↩️ Вернуться к проверке", "message_review_return"),
            ("✖️ Отменить черновик", "message_discard_confirm"),
        ),
    )
    await callback.answer()


@router.message(MessageStates.waiting_message)
async def message_send(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if len(text) < 20:
        await message.answer(
            "Опишите вопрос подробнее — минимум 20 символов. Черновик не отправлен.",
            reply_markup=one(
                ("⬅️ Назад к срочности", "message_back_urgency"),
                ("Отменить действие", "nav_cancel"),
            ),
        )
        return
    if len(text) > 4000:
        await message.answer(
            "Сообщение слишком длинное. Сократите его до 4000 символов. Черновик не отправлен.",
            reply_markup=one(
                ("⬅️ Назад к срочности", "message_back_urgency"),
                ("Отменить действие", "nav_cancel"),
            ),
        )
        return

    await state.update_data(
        draft_text=text,
        source_message_id=int(message.message_id),
    )
    await state.set_state(MessageStates.confirming_message)
    data = await state.get_data()
    await message.answer(
        _draft_review_text(data),
        reply_markup=_review_markup(),
    )


@router.callback_query(lambda c: c.data == "message_review_return")
async def message_review_return(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    if not data.get("draft_text"):
        if data.get("category") and data.get("urgency"):
            await state.set_state(MessageStates.waiting_message)
            await callback.message.edit_text(
                "Черновик текста не найден. Отправьте текст вопроса заново.\n\n"
                + _message_prompt(data),
                reply_markup=one(
                    ("⬅️ Назад к срочности", "message_back_urgency"),
                    ("Отменить действие", "nav_cancel"),
                ),
            )
            await callback.answer("Нужен текст вопроса.")
            return
        await message_create(callback, state, db)
        return

    await state.set_state(MessageStates.confirming_message)
    await callback.message.edit_text(
        _draft_review_text(data),
        reply_markup=_review_markup(),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "message_discard_confirm")
async def message_discard_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("draft_text"):
        await state.clear()
        await callback.message.edit_text(
            "Активного черновика уже нет. Ничего не отправлено.",
            reply_markup=one(
                ("✉️ Новый вопрос", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "✖️ Отменить черновик?\n\n"
        "Вопрос ещё не отправлен. После удаления восстановить этот черновик из бота не получится.",
        reply_markup=one(
            ("↩️ Вернуться к проверке", "message_review_return"),
            ("🗑 Удалить черновик", "message_discard"),
        ),
    )
    await callback.answer()


@router.callback_query(lambda c: c.data == "message_discard")
async def message_discard(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "Черновик удалён. Ничего не отправлено.",
        reply_markup=one(
            ("✉️ Новый вопрос", "message_create"),
            ("💬 Юридическая помощь", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ),
    )
    await callback.answer("Черновик удалён.")


@router.callback_query(
    lambda c: str(c.data or "").startswith("message_retarget_current:v2:")
)
async def message_retarget_current(callback: CallbackQuery, state: FSMContext, db):
    """Retarget a preserved draft only after an explicit exact-Case choice."""

    data = await state.get_data()
    if not str(data.get("draft_text") or "").strip():
        await state.clear()
        await callback.message.edit_text(
            "Черновик уже отсутствует. Ничего не перенесено и не отправлено.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    scope = await resolve_case_callback_scope(
        callback,
        db,
        action="message_retarget_current",
    )
    if scope is None or scope.case is None:
        return
    case = scope.case
    case_id = int(case.id)
    case_number = str(case.case_number)
    await state.update_data(
        case_id=case_id,
        case_number=case_number,
        new_request_confirmed=False,
        client_message_case_id=case_id,
        client_message_recovery_case_id=None,
    )
    await state.set_state(MessageStates.confirming_message)
    data = await state.get_data()
    await callback.message.edit_text(
        "✅ КОНТЕКСТ ЧЕРНОВИКА ИЗМЕНЁН\n"
        f"Обращение № {case_number}\n\n"
        "СЕЙЧАС\n"
        "Черновик перенесён только в выбранный контекст, но ещё не отправлен.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Ещё раз проверьте тему, срочность и текст, затем подтвердите отправку.\n\n"
        + _draft_review_text(data),
        reply_markup=_review_markup(),
    )
    await callback.answer("Контекст изменён. Черновик не отправлен.")


@router.callback_query(lambda c: c.data == "message_retarget_new_confirm")
async def message_retarget_new_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not str(data.get("draft_text") or "").strip():
        await callback.message.edit_text(
            "Черновик уже отсутствует. Новое обращение не создано.",
            reply_markup=one(
                ("✉️ Новый вопрос", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await state.set_state(MessageStates.confirming_message)
    await callback.message.edit_text(
        "🆕 Перенести сохранённый черновик в новое обращение?\n\n"
        "Исходное дело останется закрытым и не изменится. После подтверждения вы снова увидите текст "
        "и отдельно нажмёте «Отправить вопрос» — автоматической отправки не будет.",
        reply_markup=one(
            ("Да, подготовить новое обращение", "message_retarget_new"),
            ("↩️ Вернуться к черновику", "message_review_return"),
            ("✖️ Отменить черновик", "message_discard_confirm"),
        ),
    )


@router.callback_query(lambda c: c.data == "message_retarget_new")
async def message_retarget_new(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    if not str(data.get("draft_text") or "").strip():
        await state.clear()
        await callback.message.edit_text(
            "Черновик уже отсутствует. Новое обращение не создано.",
            reply_markup=one(
                ("✉️ Новый вопрос", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active = await ctx.case_service.get_active_case_for_user(user.id)
    if active:
        await state.set_state(MessageStates.confirming_message)
        await callback.message.edit_text(
            "📁 Уже появилось активное дело. Новый кейс не создан, а черновик не прикреплён к нему. "
            "Вернитесь к черновику или откройте текущее дело и решите контекст явно.",
            reply_markup=one(
                (
                    f"➡️ Перенести в {active.case_number}",
                    f"message_retarget_current:v2:{int(active.id)}",
                ),
                ("↩️ Вернуться к черновику", "message_review_return"),
                ("📁 Моё дело", "my_case_open"),
                ("✖️ Отменить черновик", "message_discard_confirm"),
            ),
        )
        return

    await state.update_data(
        case_id=None,
        case_number=None,
        new_request_confirmed=True,
        client_message_case_id=None,
        client_message_recovery_case_id=None,
    )
    await state.set_state(MessageStates.confirming_message)
    data = await state.get_data()
    await callback.message.edit_text(
        "🆕 Черновик подготовлен для нового обращения. Ничего ещё не отправлено.\n\n"
        + _draft_review_text(data),
        reply_markup=_review_markup(),
    )


@router.callback_query(lambda c: c.data == "message_submit")
async def message_submit(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    text = str(data.get("draft_text") or "").strip()
    source_message_id = data.get("source_message_id")
    if not text or source_message_id is None:
        await state.clear()
        await callback.message.edit_text(
            "Этот черновик уже отправлен, удалён или больше не активен. "
            "Повторная отправка не выполнена.",
            reply_markup=one(
                ("🗂 Открыть переписку", "message_history"),
                ("✉️ Новый вопрос", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        await callback.answer("Активного черновика нет.")
        return

    current_state = await state.get_state()
    if current_state != MessageStates.confirming_message.state:
        await callback.answer(
            "Сначала завершите текущее изменение черновика.",
            show_alert=True,
        )
        return

    category = str(data.get("category") or "Другой вопрос")
    urgency = str(data.get("urgency") or "Обычный")
    structured_text = f"Тема: {category}\nСрочность: {urgency}\n\n{text}"

    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        case = await _locked_message_target(db, ctx, user, data)

        created, is_new = await MessageService(db).get_or_create_client_message(
            case=case,
            user_id=user.id,
            text=structured_text,
            source_message_id=int(source_message_id),
        )
        notifications = []
        if is_new:
            assignment = (
                "Назначенный юрист и администратор"
                if case.assigned_lawyer_id
                else "Юрист ещё не назначен — требуется распределение"
            )
            notifications = await NotificationEngine(db).emit(
                event_code="CLIENT_MESSAGE_RECEIVED",
                case_id=case.id,
                user_id=user.id,
                payload={
                    "case_number": case.case_number,
                    "category": category,
                    "urgency": urgency,
                    "assignment": assignment,
                    "text": _truncate(text, NOTIFICATION_TEXT_LIMIT),
                },
                dedupe_key=f"case:{case.id}:message:{created.id}:client-message",
            )
        notification_ids = tuple(
            int(item.id) for item in notifications if item.id is not None
        )
        message_id = int(created.id)
        case_id = int(case.id)
        case_number = str(case.case_number)
        lawyer_assigned = bool(case.assigned_lawyer_id)
        await db.commit()
    except MessageTargetChanged as error:
        await db.rollback()
        await state.set_state(MessageStates.confirming_message)
        recovery_ctx = BotContextService(db)
        recovery_user = await recovery_ctx.get_user_from_callback(callback)
        current_case = await recovery_ctx.case_service.get_active_case_for_user(
            recovery_user.id
        )
        active_cases = await recovery_ctx.case_service.get_active_cases_for_user(
            recovery_user.id
        )
        buttons: list[tuple[str, str]] = []
        if current_case is not None:
            buttons.append(
                (
                    f"➡️ Перенести в {current_case.case_number}",
                    f"message_retarget_current:v2:{int(current_case.id)}",
                )
            )
        elif not active_cases:
            buttons.append(("🆕 Перенести в новое обращение", "message_retarget_new_confirm"))
        if len(active_cases) > 1:
            buttons.append(("📁 Выбрать обращение", "my_cases_open"))
        buttons.extend(
            [
                ("↩️ Вернуться к черновику", "message_review_return"),
                ("📁 Моё дело", "my_case_open"),
                ("✖️ Отменить черновик", "message_discard_confirm"),
            ]
        )
        await _replace_or_send(
            callback,
            "⚠️ ВОПРОС НЕ ОТПРАВЛЕН\n\n"
            f"{error}\n\n"
            "СЕЙЧАС\n"
            "Черновик сохранён и не перенесён в другое дело автоматически.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Выберите точный контекст отдельной кнопкой или вернитесь к черновику.",
            reply_markup=one(*buttons),
        )
        await callback.answer("Отправка остановлена: дело изменилось.")
        return
    except Exception:
        await db.rollback()
        await state.set_state(MessageStates.confirming_message)
        await _replace_or_send(
            callback,
            "Не удалось зарегистрировать вопрос. Ничего не отправлено, а черновик сохранён. "
            "Можно повторить попытку или изменить текст.",
            reply_markup=one(
                ("🔄 Повторить отправку", "message_submit"),
                ("✏️ Изменить текст", "message_edit_text"),
                ("↩️ Вернуться к проверке", "message_review_return"),
                ("✖️ Отменить черновик", "message_discard_confirm"),
            ),
        )
        await callback.answer("Отправка не выполнена.")
        return

    try:
        await state.clear()
    except Exception:
        pass

    delivery = (
        await deliver_selected_notifications(db, notification_ids)
        if is_new
        else {"status": "not_required"}
    )
    confirmation = (
        "✅ ВОПРОС ЗАРЕГИСТРИРОВАН\n"
        f"Обращение № {case_number}\n\n"
        "СЕЙЧАС\n"
        f"Сообщение #{message_id} сохранено.\n"
        f"Тема: {category}\n"
        f"Срочность: {urgency}\n"
        f"{_delivery_status_text(delivery, created=is_new)}\n\n"
    )
    if lawyer_assigned:
        confirmation += "Ответственный юрист уже назначен."
    else:
        confirmation += (
            "Юрист ещё не назначен. Вопрос направлен в административную "
            "очередь на распределение."
        )
    confirmation += (
        "\n\nГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Следите за ответом в переписке по делу; уведомление также придёт в Telegram."
    )
    if urgency == "Критично: срок менее 24 часов":
        confirmation += (
            "\n\n⚠️ Срочность зафиксирована. Если официальный срок истекает сегодня, "
            "не ждите только ответа в боте — используйте доступный "
            "официальный способ подачи документов."
        )

    await _replace_or_send(
        callback,
        confirmation,
        reply_markup=one(
            ("🗂 Открыть переписку", "message_history"),
            (
                "✉️ Написать ещё",
                bound_case_callback("message_create", case_id),
            ),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
    await callback.answer("Вопрос отправлен.")