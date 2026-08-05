from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.client_case_view import (
    format_updated_at,
    load_client_case_view,
    next_action_text,
    progress_bar,
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


def _primary_action(view) -> tuple[str, str]:
    if view.unread_team_messages:
        return (
            f"💬 Прочитать ответ команды ({view.unread_team_messages})",
            "message_history",
        )
    if view.action:
        return (
            f"▶️ {view.action.label}",
            f"next_action:v2:{view.case_id}:{view.action_key}",
        )
    return ("📁 Открыть текущее дело", "my_case_open")


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


async def _home_text(
    db,
    message_or_callback,
) -> tuple[str, bool, tuple[str, str] | None]:
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
        lines = [
            "🏠 Главная",
            "",
            f"📁 Дело № {view.case_number}",
            f"Услуга: {view.route_label}",
            "",
            "Текущий этап",
            view.status_label,
            progress_bar(view.progress_percent),
            "",
            "📌 Ваш следующий шаг",
            view.next_action,
        ]
        if view.unread_team_messages:
            lines.extend(
                [
                    "",
                    f"💬 Новые ответы команды: {view.unread_team_messages}",
                    "Сначала откройте переписку: ответ может уточнять документы, сроки или дальнейшие действия.",
                ]
            )
        if view.documents.blocker:
            lines.extend(
                [
                    "",
                    f"⚠️ Что мешает продолжить: {view.documents.blocker}",
                ]
            )
        lines.extend(
            [
                "",
                f"📄 Документы: {view.documents.summary}",
            ]
        )
        if view.route == "M2" or view.consultation_summary != "Не назначена":
            lines.append(f"🗓 Консультация: {view.consultation_summary}")
        if view.payments_summary:
            lines.append(f"💳 Оплаты: {view.payments_summary}")
        lines.extend(
            [
                "",
                f"Обновлено: {format_updated_at(view.updated_at)}",
                "Главная кнопка ниже ведёт к самому актуальному действию.",
            ]
        )
        return "\n".join(lines), True, _primary_action(view)
    text = (
        "🏠 Добро пожаловать\n\n"
        "Я помогу предварительно рассчитать неустойку по ДДУ, передать документы юристу "
        "и отслеживать ход дела прямо в Telegram.\n\n"
        "Расчёт предварительный и не является юридическим заключением."
    )
    return text, False, None


@router.message(lambda m: m.text in ["/start", "/menu", "🏠 Главная"])
async def start(message: Message, db, state: FSMContext):
    await state.clear()
    text, case_exists, primary_action = await _home_text(db, message)
    await db.commit()
    await message.answer(text, reply_markup=reply_main_menu())
    await message.answer(
        "Выберите действие:",
        reply_markup=main_menu(
            case_exists,
            primary_action=primary_action,
        ),
    )


@router.message(lambda m: m.text == "🧮 Рассчитать неустойку")
async def menu_calc(message: Message, state: FSMContext, db):
    await state.clear()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        view = await load_client_case_view(db, case)
        await db.commit()
        await message.answer(
            "📁 У вас уже есть активное дело.\n\n"
            "Чтобы не смешивать расчёты, документы и статусы разных обращений, "
            "сначала продолжите текущее дело. Новый расчёт станет доступен после "
            "его завершения.",
            reply_markup=main_menu(
                True,
                primary_action=_primary_action(view),
            ),
        )
        return
    await db.commit()
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
        "Откройте единый экран дела: текущий этап, готовность, новые ответы и следующее действие.",
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
        "📁 Моё дело — текущий этап, готовность, ответы и следующее действие.\n"
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
    lines = [
        f"📁 {view.case_number}",
        f"Услуга: {view.route_label}",
        f"Сейчас: {view.status_label}",
        f"Документы: {view.documents.summary}",
    ]
    if view.unread_team_messages:
        lines.append(f"💬 Новые ответы команды: {view.unread_team_messages}")
    lines.extend(["", f"Следующий шаг: {view.next_action}"])
    buttons: list[tuple[str, str]] = []
    if view.unread_team_messages:
        buttons.append(
            (
                f"💬 Прочитать ответы ({view.unread_team_messages})",
                "message_history",
            )
        )
    buttons.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await message.answer(
        "\n".join(lines),
        reply_markup=one(*buttons),
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
    text, case_exists, primary_action = await _home_text(db, callback)
    await _safe_callback_edit(
        callback,
        text,
        reply_markup=main_menu(
            case_exists,
            primary_action=primary_action,
        ),
    )


@router.callback_query(lambda c: c.data == "noop")
async def noop(callback: CallbackQuery, db, state: FSMContext):
    await state.clear()
    text, case_exists, primary_action = await _home_text(db, callback)
    await _safe_callback_edit(
        callback,
        "Эта кнопка больше не актуальна. Показано текущее состояние.\n\n" + text,
        reply_markup=main_menu(
            case_exists,
            primary_action=primary_action,
        ),
        unchanged_notice="Показано текущее состояние.",
    )


@router.callback_query(lambda c: c.data == "nav_cancel")
async def cancel(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    text, case_exists, primary_action = await _home_text(db, callback)
    await _safe_callback_edit(
        callback,
        "Действие отменено. Уже сохранённые данные не удалены.\n\n" + text,
        reply_markup=main_menu(
            case_exists,
            primary_action=primary_action,
        ),
        unchanged_notice="Действие уже отменено.",
    )


@router.callback_query(lambda c: c.data == "nav_back")
async def back(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    text, case_exists, primary_action = await _home_text(db, callback)
    await _safe_callback_edit(
        callback,
        text,
        reply_markup=main_menu(
            case_exists,
            primary_action=primary_action,
        ),
    )