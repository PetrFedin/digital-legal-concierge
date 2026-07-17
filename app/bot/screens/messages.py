from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.messages.message_service import MessageService

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


def _format_dialog(messages) -> str:
    if not messages:
        return "💬 Переписка по делу\n\nСообщений пока нет."

    lines = ["💬 Переписка по делу\n"]
    for item in messages[-20:]:
        author = "Вы" if item.sender_type == "client" else "Юрист"
        created_at = item.created_at.strftime("%d.%m.%Y %H:%M") if item.created_at else ""
        lines.append(f"{author} · {created_at}\n{item.text}")
    return "\n\n".join(lines)


@router.callback_query(lambda c: c.data == "contact_lawyer")
async def contact_lawyer(callback: CallbackQuery, db, state: FSMContext):
    await state.clear()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        await callback.message.edit_text(
            "💬 Связаться с юристом\n\n"
            "Можно написать сообщение по текущему делу, открыть переписку "
            "или записаться на платную консультацию.",
            reply_markup=one(
                ("✉️ Написать по текущему делу", "message_create"),
                ("🗂 Открыть переписку", "message_history"),
                ("📅 Записаться на консультацию", "consult_booking_start"),
                ("📁 Мое дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
    else:
        await callback.message.edit_text(
            "💬 Личная консультация\n\n"
            "Выберите свободные дату и время, оплатите встречу, затем укажите дело и конкретный вопрос.",
            reply_markup=one(
                ("📅 Выбрать дату и время", "consult_booking_start"),
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
            "Активного дела пока нет.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    service = MessageService(db)
    messages = await service.list_case_messages(case.id)
    await service.mark_lawyer_messages_read(case.id)
    await db.commit()
    await callback.message.edit_text(
        _format_dialog(messages),
        reply_markup=one(
            ("✉️ Написать сообщение", "message_create"),
            ("🔄 Обновить", "message_history"),
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "message_create")
async def message_create(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(MessageStates.choosing_category)
    await callback.message.edit_text(
        "✉️ Новый вопрос юристу\n\nВыберите тему обращения:",
        reply_markup=one(
            ("📄 Документы", "msg_cat_documents"),
            ("⏰ Сроки и заседание", "msg_cat_deadline"),
            ("💳 Оплата и возврат", "msg_cat_payment"),
            ("📁 Ход дела", "msg_cat_case"),
            ("❓ Другой вопрос", "msg_cat_other"),
            ("Отменить действие", "nav_cancel"),
        ),
    )


@router.callback_query(MessageStates.choosing_category, lambda c: c.data in MESSAGE_CATEGORIES)
async def message_category(callback: CallbackQuery, state: FSMContext):
    await state.update_data(category=MESSAGE_CATEGORIES[callback.data])
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


@router.callback_query(MessageStates.choosing_urgency, lambda c: c.data in URGENCY_LEVELS)
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
        confirmation += "\n\n⚠️ Если срок процессуального действия истекает сегодня, не ждите только ответа в боте — используйте доступный официальный способ подачи документов."

    await message.answer(
        confirmation,
        reply_markup=one(
            ("🗂 Открыть переписку", "message_history"),
            ("✉️ Написать еще", "message_create"),
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
