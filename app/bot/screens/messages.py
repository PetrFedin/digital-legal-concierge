from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.messages.message_service import MessageService

router = Router()


class MessageStates(StatesGroup):
    waiting_message = State()


@router.callback_query(lambda c: c.data == "contact_lawyer")
async def contact_lawyer(callback: CallbackQuery, db, state: FSMContext):
    await state.clear()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        await callback.message.edit_text(
            "💬 Связаться с юристом\n\n"
            "Можно написать сообщение по текущему делу или записаться на платную консультацию. "
            "Сообщение будет зарегистрировано в истории дела.",
            reply_markup=one(
                ("✉️ Написать по текущему делу", "message_create"),
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


@router.callback_query(lambda c: c.data == "message_create")
async def message_create(callback: CallbackQuery, state: FSMContext):
    await state.set_state(MessageStates.waiting_message)
    await callback.message.edit_text(
        "✉️ Напишите вопрос по текущему делу.\n\n"
        "Укажите, что произошло, какой результат вы ожидаете и есть ли срочный срок. "
        "Сообщение будет сохранено в истории дела.",
        reply_markup=one(("Отменить действие", "nav_cancel")),
    )


@router.message(MessageStates.waiting_message)
async def message_send(message: Message, state: FSMContext, db):
    text = (message.text or "").strip()
    if len(text) < 10:
        await message.answer(
            "Опишите вопрос подробнее — минимум 10 символов.",
            reply_markup=one(("Отменить действие", "nav_cancel")),
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        case = await ctx.get_or_create_active_case_for_user(user)

    created = await MessageService(db).create_client_message(
        case=case,
        user_id=user.id,
        text=text,
    )
    await db.commit()
    await state.clear()

    await message.answer(
        "✅ Вопрос зарегистрирован.\n\n"
        f"Номер сообщения: #{created.id}\n"
        f"Дело: {case.case_number}\n"
        "Статус: ожидает просмотра командой.\n\n"
        "Ответ появится в Telegram после обработки юристом.",
        reply_markup=one(
            ("✉️ Написать еще", "message_create"),
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
