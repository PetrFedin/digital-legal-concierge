from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.messages.message_service import MessageService
from app.domain.payments.mode import payments_disabled

router = Router()


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


def _format_dialog(messages) -> str:
    if not messages:
        return (
            "💬 Переписка по делу\n\n"
            "Сообщений пока нет. Вы можете отправить первый вопрос юристу."
        )

    lines = ["💬 Переписка по делу\n"]
    for item in messages[-20:]:
        author = "Вы" if item.sender_type == "client" else "Юрист"
        created_at = (
            item.created_at.strftime("%d.%m.%Y %H:%M")
            if item.created_at
            else ""
        )
        lines.append(f"{author} · {created_at}\n{item.text}")
    return "\n\n".join(lines)


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
            "💬 Связаться с юристом\n\n"
            "Здесь можно написать сообщение по текущему делу, открыть переписку "
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


@router.callback_query(lambda c: c.data == "message_history")
async def message_history(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
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
        messages = await service.list_case_messages(case.id)
        await service.mark_lawyer_messages_read(case.id)
        await db.commit()
    except Exception:
        await db.rollback()
        await callback.message.edit_text(
            "Не удалось загрузить переписку. Данные не изменены.",
            reply_markup=one(
                ("🔄 Повторить", "message_history"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        _format_dialog(messages),
        reply_markup=one(
            ("✉️ Написать сообщение", "message_create"),
            ("🔄 Обновить", "message_history"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


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
        "✉️ Новый вопрос юристу\n\n"
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

        created = await MessageService(db).create_client_message(
            case=case,
            user_id=user.id,
            text=structured_text,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        await message.answer(
            "Не удалось отправить вопрос. Текст не был зарегистрирован. "
            "Скопируйте его и повторите отправку.",
            reply_markup=one(
                ("🔄 Начать отправку заново", "message_create"),
                ("Отменить действие", "nav_cancel"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await state.clear()
    confirmation = (
        "✅ Вопрос зарегистрирован.\n\n"
        f"Номер сообщения: #{created.id}\n"
        f"Дело: {case.case_number}\n"
        f"Тема: {category}\n"
        f"Срочность: {urgency}\n"
        "Статус: ожидает ответа юриста.\n\n"
        "Ответ появится в Telegram и в переписке по делу."
    )
    if urgency == "Критично: срок менее 24 часов":
        confirmation += (
            "\n\n⚠️ Если срок процессуального действия истекает сегодня, "
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
