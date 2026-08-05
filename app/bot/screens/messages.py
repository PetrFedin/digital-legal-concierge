from __future__ import annotations

from math import ceil

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.messages.message_service import MessageService
from app.domain.notifications.immediate_delivery import deliver_selected_notifications
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.mode import payments_disabled

router = Router()

HISTORY_PAGE_SIZE = 5
HISTORY_ITEM_TEXT_LIMIT = 560
HISTORY_TEXT_LIMIT = 3800
NOTIFICATION_TEXT_LIMIT = 3000

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


class MessageStates(StatesGroup):
    choosing_category = State()
    choosing_urgency = State()
    waiting_message = State()


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


def _format_dialog(messages, requested_page: int = 0) -> tuple[str, int, int]:
    page_messages, page, total_pages = _history_slice(messages, requested_page)
    if not page_messages:
        return (
            "💬 Переписка по делу\n\n"
            "Сообщений пока нет. Вы можете отправить первый вопрос команде.",
            page,
            total_pages,
        )

    lines = [
        "💬 Переписка по делу",
        f"Страница {page + 1} из {total_pages}. Первая страница — самые новые сообщения.",
    ]
    for item in page_messages:
        author = "Вы" if item.sender_type == "client" else "Команда"
        created_at = (
            item.created_at.strftime("%d.%m.%Y %H:%M")
            if item.created_at
            else "время не указано"
        )
        body = _truncate(item.text, HISTORY_ITEM_TEXT_LIMIT)
        lines.append(f"{author} · {created_at}\n{body}")
    return _truncate("\n\n".join(lines), HISTORY_TEXT_LIMIT), page, total_pages


def _history_keyboard(page: int, total_pages: int):
    buttons: list[tuple[str, str]] = []
    if page < total_pages - 1:
        buttons.append(("⬅️ Более ранние", f"message_history:{page + 1}"))
    if page > 0:
        buttons.append(("Более новые ➡️", f"message_history:{page - 1}"))
    buttons.extend(
        [
            ("✉️ Написать сообщение", "message_create"),
            ("🔄 Обновить", f"message_history:{page}"),
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


@router.callback_query(lambda c: c.data == "contact_lawyer")
async def contact_lawyer(callback: CallbackQuery, db, state: FSMContext):
    await state.clear()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)

    consultation_note = (
        "записаться на консультацию без онлайн-оплаты"
        if payments_disabled()
        else "записаться на консультацию"
    )
    if case:
        await callback.message.edit_text(
            "💬 Связаться с юридической командой\n\n"
            "Здесь можно написать по текущему делу, открыть переписку "
            f"или {consultation_note}.",
            reply_markup=one(
                ("✉️ Написать по текущему делу", "message_create"),
                ("🗂 Открыть переписку", "message_history"),
                ("📅 Записаться на консультацию", "consult_booking_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        "💬 Юридическая помощь\n\n"
        "Активного дела пока нет. Можно задать вопрос — после отправки будет "
        "создано новое обращение, либо сразу выбрать время консультации.",
        reply_markup=one(
            ("✉️ Задать вопрос", "message_create"),
            ("📅 Выбрать время консультации", "consult_booking_start"),
            ("🧮 Рассчитать неустойку", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: bool(c.data)
    and (c.data == "message_history" or c.data.startswith("message_history:"))
)
async def message_history(callback: CallbackQuery, db):
    requested_page = _history_page_from_callback(callback.data)
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await _safe_edit(
            callback,
            "Переписки пока нет, потому что активное дело не создано.",
            reply_markup=one(
                ("✉️ Задать вопрос", "message_create"),
                ("📅 Записаться на консультацию", "consult_booking_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        service = MessageService(db)
        messages = await service.list_case_messages(case.id, limit=100)
        await service.mark_lawyer_messages_read(case.id)
        await db.commit()
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

    text, page, total_pages = _format_dialog(messages, requested_page)
    markup = _history_keyboard(page, total_pages)
    try:
        changed = await _safe_edit(callback, text, reply_markup=markup)
        if changed:
            await callback.answer()
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup)
        await callback.answer("Переписка открыта новым сообщением.")


@router.callback_query(lambda c: c.data == "message_create")
async def message_create(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    await state.clear()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    await state.set_state(MessageStates.choosing_category)
    case_note = (
        f"Вопрос будет добавлен к делу {case.case_number}."
        if case
        else "После отправки вопроса будет создано новое обращение."
    )
    await callback.message.edit_text(
        "✉️ Новый вопрос юридической команде\n\n"
        f"{case_note}\n\n"
        "Выберите тему обращения:",
        reply_markup=one(*_category_buttons()),
    )


@router.callback_query(
    MessageStates.choosing_category,
    lambda c: c.data in MESSAGE_CATEGORIES,
)
async def message_category(callback: CallbackQuery, state: FSMContext):
    await state.update_data(category=_category_title(callback.data))
    await state.set_state(MessageStates.choosing_urgency)
    await callback.message.edit_text(
        "Насколько срочно нужен ответ?",
        reply_markup=one(
            ("Обычный вопрос", "msg_urgency_normal"),
            ("Нужен ответ сегодня", "msg_urgency_soon"),
            ("Критично: срок менее 24 часов", "msg_urgency_critical"),
            ("Отменить действие", "nav_cancel"),
        ),
    )


@router.callback_query(
    MessageStates.choosing_urgency,
    lambda c: c.data in URGENCY_LEVELS,
)
async def message_urgency(callback: CallbackQuery, state: FSMContext):
    await state.update_data(urgency=URGENCY_LEVELS[callback.data])
    await state.set_state(MessageStates.waiting_message)
    await callback.message.edit_text(
        "Теперь опишите вопрос.\n\n"
        "Укажите, что произошло, какой результат вы ожидаете, важные даты и документы. "
        "Не отправляйте пароли, коды из SMS и банковские данные.",
        reply_markup=one(("Отменить действие", "nav_cancel")),
    )


@router.message(MessageStates.waiting_message)
async def message_send(message: Message, state: FSMContext, db):
    text = (message.text or "").strip()
    if len(text) < 20:
        await message.answer(
            "Опишите вопрос подробнее — минимум 20 символов.",
            reply_markup=one(("Отменить действие", "nav_cancel")),
        )
        return
    if len(text) > 4000:
        await message.answer(
            "Сообщение слишком длинное. Сократите его до 4000 символов.",
            reply_markup=one(("Отменить действие", "nav_cancel")),
        )
        return

    data = await state.get_data()
    category = data.get("category", "Другой вопрос")
    urgency = data.get("urgency", "Обычный")
    structured_text = f"Тема: {category}\nСрочность: {urgency}\n\n{text}"

    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_message(message)
        case = await ctx.case_service.get_active_case_for_user(user.id)
        if not case:
            case = await ctx.get_or_create_active_case_for_user(user)

        created, is_new = await MessageService(db).get_or_create_client_message(
            case=case,
            user_id=user.id,
            text=structured_text,
            source_message_id=message.message_id,
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
        case_number = str(case.case_number)
        lawyer_assigned = bool(case.assigned_lawyer_id)
        await db.commit()
    except Exception:
        await db.rollback()
        await message.answer(
            "Не удалось зарегистрировать вопрос. Текст не сохранён. "
            "Скопируйте его и повторите отправку.",
            reply_markup=one(
                ("🔄 Начать отправку заново", "message_create"),
                ("Отменить действие", "nav_cancel"),
                ("🏠 Главная", "nav_home"),
            ),
        )
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
        "✅ Вопрос зарегистрирован.\n\n"
        f"Номер сообщения: #{message_id}\n"
        f"Дело: {case_number}\n"
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
        "\n\nОтвет появится в переписке по делу и будет продублирован в Telegram."
    )
    if urgency == "Критично: срок менее 24 часов":
        confirmation += (
            "\n\n⚠️ Срочность зафиксирована. Если официальный срок истекает сегодня, "
            "не ждите только ответа в боте — используйте доступный "
            "официальный способ подачи документов."
        )

    await message.answer(
        confirmation,
        reply_markup=one(
            ("🗂 Открыть переписку", "message_history"),
            ("✉️ Написать ещё", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
