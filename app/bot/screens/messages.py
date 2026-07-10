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
async def contact_lawyer(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        await callback.message.edit_text(
            "💬 Связаться с юристом\n\n"
            "Можно написать сообщение по текущему делу или записаться на платную консультацию. "
            "Для записи сначала выбираются свободные дата и время.",
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
        "✉️ Напишите вопрос по делу. Сообщение будет сохранено в истории.",
        reply_markup=one(("Отмена", "nav_home")),
    )


@router.message(MessageStates.waiting_message)
async def message_send(message: Message, state: FSMContext, db):
    text = (message.text or "").strip()
    if len(text) < 2:
        await message.answer("Введите текст сообщения.")
        return
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        case = await ctx.get_or_create_active_case_for_user(user)
    await MessageService(db).create_client_message(case=case, user_id=user.id, text=text)
    await db.commit()
    await state.clear()
    await message.answer(
        "✅ Сообщение сохранено и передано команде.",
        reply_markup=one(("📁 Мое дело", "my_case_open"), ("🏠 Главная", "nav_home")),
    )
