from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import main_menu, one, reply_main_menu
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.payments.mode import payments_disabled

router = Router()


PILOT_NEXT_ACTIONS = {
    "M1_WAITING_PAYMENT_30000": "Продолжить оформление доверенности",
    "M1_WAITING_PAYMENT_70000": "Продолжить этап исполнения решения",
    "M1_WAITING_SUCCESS_FEE": "Завершить финансовый этап",
    "M2_PAYMENT_PENDING": "Подтвердить запись на консультацию",
}


def _route_label(route: str | None) -> str:
    return {
        "M1": "Ведение дела",
        "M2": "Консультация",
    }.get(str(route or ""), "Юридическое обращение")


def _next_action(case) -> str:
    if payments_disabled() and str(case.status) in PILOT_NEXT_ACTIONS:
        return PILOT_NEXT_ACTIONS[str(case.status)]
    return case.next_action or "Откройте «Моё дело» для актуального шага"


async def _home_text(db, message_or_callback) -> tuple[str, bool]:
    ctx = BotContextService(db)
    if hasattr(message_or_callback, "from_user") and hasattr(
        message_or_callback,
        "message",
    ):
        user = await ctx.get_user_from_callback(message_or_callback)
    else:
        user = await ctx.get_user_from_message(message_or_callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        text = (
            "🏠 Главная\n\n"
            f"Активное дело: {case.case_number}\n"
            f"Услуга: {_route_label(case.route)}\n"
            f"Статус: {get_client_visible_status(case.status)}\n"
            f"Ближайший шаг: {_next_action(case)}\n\n"
            "Откройте «Моё дело» или выберите другое действие."
        )
        return text, True
    text = (
        "🏠 Добро пожаловать\n\n"
        "Я помогу предварительно рассчитать неустойку по ДДУ, передать документы юристу "
        "и отслеживать ход дела прямо в Telegram.\n\n"
        "Расчёт предварительный и не является юридическим заключением."
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
    await message.answer(
        "Начните предварительный расчёт или вернитесь на главную.",
        reply_markup=one(
            ("Начать расчёт", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(lambda m: m.text in ["📁 Мое дело", "📁 Моё дело"])
async def menu_my_case(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Откройте актуальный статус, документы и следующий шаг.",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(lambda m: m.text == "📄 Документы")
async def menu_documents(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "В разделе документов можно добавить файлы и проверить их статус.",
        reply_markup=one(
            ("📄 Открыть документы", "documents_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(lambda m: m.text == "💬 Связаться с юристом")
async def menu_lawyer(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Выберите способ связи или вернитесь на главную.",
        reply_markup=one(
            ("💬 Открыть связь с юристом", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(lambda m: m.text == "/help")
async def help_command(message: Message):
    payment_line = (
        "Онлайн-оплата сейчас отключена; доступные этапы продолжаются без платёжной ссылки."
        if payments_disabled()
        else "Оплата доступна на соответствующих этапах дела."
    )
    await message.answer(
        "ℹ️ Помощь\n\n"
        "Основные разделы:\n"
        "🧮 Рассчитать неустойку — предварительный расчёт.\n"
        "📁 Моё дело — статус, следующий шаг, документы и история.\n"
        "📄 Документы — загрузка копий и сканов.\n"
        "💬 Связаться с юристом — вопрос по делу или консультация.\n\n"
        f"{payment_line}\n\n"
        "Команды: /start, /menu, /status, /help, /cancel",
        reply_markup=reply_main_menu(),
    )


@router.message(lambda m: m.text == "/status")
async def status_command(message: Message, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await message.answer(
            "Активного дела пока нет.",
            reply_markup=one(
                ("🧮 Рассчитать", "calc_start"),
                ("💬 Консультация", "calc_to_m2"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await message.answer(
        f"📁 {case.case_number}\n"
        f"Услуга: {_route_label(case.route)}\n"
        f"Статус: {get_client_visible_status(case.status)}\n"
        f"Ближайший шаг: {_next_action(case)}",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(lambda m: m.text in ["/cancel", "Отмена"])
async def cancel_message(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Действие отменено. Уже сохранённые данные не удалены.",
        reply_markup=reply_main_menu(),
    )


@router.callback_query(lambda c: c.data == "nav_home")
async def home(callback: CallbackQuery, db, state: FSMContext):
    await state.clear()
    text, case_exists = await _home_text(db, callback)
    await callback.message.edit_text(text, reply_markup=main_menu(case_exists))


@router.callback_query(lambda c: c.data == "noop")
async def noop(callback: CallbackQuery, db, state: FSMContext):
    await state.clear()
    text, case_exists = await _home_text(db, callback)
    await callback.message.edit_text(
        "Эта кнопка больше не актуальна. Показано текущее состояние.\n\n" + text,
        reply_markup=main_menu(case_exists),
    )


@router.callback_query(lambda c: c.data == "nav_cancel")
async def cancel(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    text, case_exists = await _home_text(db, callback)
    await callback.message.edit_text(
        "Действие отменено. Уже сохранённые данные не удалены.\n\n" + text,
        reply_markup=main_menu(case_exists),
    )


@router.callback_query(lambda c: c.data == "nav_back")
async def back(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    text, case_exists = await _home_text(db, callback)
    await callback.message.edit_text(text, reply_markup=main_menu(case_exists))
