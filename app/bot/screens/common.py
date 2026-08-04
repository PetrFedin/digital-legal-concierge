from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.client_case_view import (
    load_client_case_view,
    next_action_text,
    route_label,
)
from app.bot.context import BotContextService
from app.bot.keyboards import main_menu, one, reply_main_menu
from app.domain.payments.mode import payments_disabled

router = Router()


# Retained as a public compatibility contract for integrations that inspect
# no-payment pilot wording. Actual client screens use the shared case view.
PILOT_NEXT_ACTIONS = {
    "M1_WAITING_PAYMENT_30000": "Продолжить оформление доверенности",
    "M1_WAITING_PAYMENT_70000": "Продолжить этап исполнения решения",
    "M1_WAITING_SUCCESS_FEE": "Завершить финансовый этап",
    "M2_PAYMENT_PENDING": "Подтвердить запись на консультацию",
}


def _route_label(route: str | None) -> str:
    return route_label(route)


def _next_action(case) -> str:
    if payments_disabled() and str(case.status) in PILOT_NEXT_ACTIONS:
        return PILOT_NEXT_ACTIONS[str(case.status)]
    return next_action_text(case)


async def _safe_callback_edit(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup,
    unchanged_notice: str = "Главный экран уже актуален.",
) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer(unchanged_notice)


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
        view = await load_client_case_view(db, case)
        text = (
            "🏠 Главная\n\n"
            f"Активное дело: {view.case_number}\n"
            f"Услуга: {view.route_label}\n"
            f"Сейчас: {view.status_label}\n"
            f"Документы: {view.documents.summary}\n\n"
            f"Ваш следующий шаг:\n{view.next_action}\n\n"
            "Откройте «Моё дело», чтобы увидеть готовность и выполнить действие."
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
        "Откройте единый экран дела: текущий этап, готовность и одно следующее действие.",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(lambda m: m.text == "📄 Документы")
async def menu_documents(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "В разделе документов видны актуальные файлы, замечания юриста и история версий.",
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
        "📁 Моё дело — текущий этап, готовность и следующее действие.\n"
        "📄 Документы — актуальные версии, замечания и история.\n"
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
    view = await load_client_case_view(db, case)
    await message.answer(
        f"📁 {view.case_number}\n"
        f"Услуга: {view.route_label}\n"
        f"Сейчас: {view.status_label}\n"
        f"Документы: {view.documents.summary}\n\n"
        f"Следующий шаг: {view.next_action}",
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
    await _safe_callback_edit(
        callback,
        text,
        reply_markup=main_menu(case_exists),
    )


@router.callback_query(lambda c: c.data == "noop")
async def noop(callback: CallbackQuery, db, state: FSMContext):
    await state.clear()
    text, case_exists = await _home_text(db, callback)
    await _safe_callback_edit(
        callback,
        "Эта кнопка больше не актуальна. Показано текущее состояние.\n\n" + text,
        reply_markup=main_menu(case_exists),
        unchanged_notice="Показано текущее состояние.",
    )


@router.callback_query(lambda c: c.data == "nav_cancel")
async def cancel(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    text, case_exists = await _home_text(db, callback)
    await _safe_callback_edit(
        callback,
        "Действие отменено. Уже сохранённые данные не удалены.\n\n" + text,
        reply_markup=main_menu(case_exists),
        unchanged_notice="Действие уже отменено.",
    )


@router.callback_query(lambda c: c.data == "nav_back")
async def back(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    text, case_exists = await _home_text(db, callback)
    await _safe_callback_edit(
        callback,
        text,
        reply_markup=main_menu(case_exists),
    )
