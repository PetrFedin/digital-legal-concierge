from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import main_menu, reply_main_menu, one
from app.domain.cases.case_timeline import get_client_visible_status

router = Router()


async def _home_text(db, message_or_callback) -> tuple[str, bool]:
    ctx = BotContextService(db)
    if hasattr(message_or_callback, "from_user") and hasattr(message_or_callback, "message"):
        user = await ctx.get_user_from_callback(message_or_callback)
    else:
        user = await ctx.get_user_from_message(message_or_callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        text = (
            "🏠 Главная\n\n"
            f"Активное дело: {case.case_number}\n"
            f"Статус: {get_client_visible_status(case.status)}\n"
            f"Следующий шаг: {case.next_action or 'ожидать обновления'}\n\n"
            "Выберите действие ниже."
        )
        return text, True
    text = (
        "🏠 Добро пожаловать\n\n"
        "Я помогу предварительно рассчитать неустойку по ДДУ, передать документы юристу "
        "и отслеживать ход дела прямо в Telegram.\n\n"
        "Расчет предварительный и не является юридическим заключением."
    )
    return text, False


@router.message(lambda m: m.text in ["/start", "/menu", "🏠 Главная"])
async def start(message: Message, db, state: FSMContext):
    await state.clear()
    text, case_exists = await _home_text(db, message)
    await db.commit()
    await message.answer(text, reply_markup=reply_main_menu())
    await message.answer("Главное меню:", reply_markup=main_menu(case_exists))


@router.message(lambda m: m.text == "🧮 Рассчитать неустойку")
async def menu_calc(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Открываю калькулятор.", reply_markup=one(("Начать расчет", "calc_start"), ("🏠 Главная", "nav_home")))


@router.message(lambda m: m.text == "📁 Мое дело")
async def menu_my_case(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Открыть раздел «Мое дело».", reply_markup=one(("📁 Мое дело", "my_case_open"), ("🏠 Главная", "nav_home")))


@router.message(lambda m: m.text == "📄 Документы")
async def menu_documents(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Открыть раздел документов.", reply_markup=one(("📄 Документы", "documents_open"), ("🏠 Главная", "nav_home")))


@router.message(lambda m: m.text == "💬 Связаться с юристом")
async def menu_lawyer(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Связь с юристом.", reply_markup=one(("💬 Открыть", "contact_lawyer"), ("🏠 Главная", "nav_home")))


@router.message(lambda m: m.text == "/help")
async def help_command(message: Message):
    await message.answer(
        "ℹ️ Помощь\n\n"
        "Основные разделы:\n"
        "🧮 Рассчитать неустойку — предварительный расчет.\n"
        "📁 Мое дело — статус, следующий шаг, документы, оплаты и история.\n"
        "📄 Документы — загрузка копий и сканов.\n"
        "💬 Связаться с юристом — вопрос по делу или консультация.\n\n"
        "Команды: /start, /menu, /status, /help, /cancel",
        reply_markup=reply_main_menu(),
    )


@router.message(lambda m: m.text == "/status")
async def status_command(message: Message, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await message.answer("Активного дела пока нет.", reply_markup=one(("🧮 Рассчитать", "calc_start"), ("💬 Консультация", "calc_to_m2")))
        return
    await message.answer(
        f"📁 {case.case_number}\n"
        f"Маршрут: {case.route or '—'}\n"
        f"Статус: {get_client_visible_status(case.status)}\n"
        f"Следующий шаг: {case.next_action or 'ожидать обновления'}",
        reply_markup=one(("📁 Мое дело", "my_case_open"), ("🏠 Главная", "nav_home")),
    )


@router.message(lambda m: m.text in ["/cancel", "Отмена"])
async def cancel_message(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено. Уже сохраненные данные не удалены.", reply_markup=reply_main_menu())


@router.callback_query(lambda c: c.data == "nav_home")
async def home(callback: CallbackQuery, db, state: FSMContext):
    await state.clear()
    text, case_exists = await _home_text(db, callback)
    await callback.message.edit_text(text, reply_markup=main_menu(case_exists))


@router.callback_query(lambda c: c.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer("В тестовом режиме используйте кнопку DEV подтверждения оплаты.", show_alert=True)


@router.callback_query(lambda c: c.data == "nav_cancel")
async def cancel(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    text, case_exists = await _home_text(db, callback)
    await callback.message.edit_text(
        "Действие отменено. Уже сохраненные данные не удалены.\n\n" + text,
        reply_markup=main_menu(case_exists),
    )


@router.callback_query(lambda c: c.data == "nav_back")
async def back(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    text, case_exists = await _home_text(db, callback)
    await callback.message.edit_text(text, reply_markup=main_menu(case_exists))
