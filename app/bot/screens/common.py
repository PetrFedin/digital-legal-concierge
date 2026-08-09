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
from app.bot.consultation_result import (
    consultation_result_view,
    is_terminal_consultation,
    latest_case_consultation,
    latest_terminal_client_consultation,
)
from app.bot.context import BotContextService
from app.bot.keyboards import main_menu, one, reply_main_menu
from app.domain.cases.client_case_scope import latest_completed_m1_case_for_user
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

CONSULTATION_RESULT_ACTION = (
    "👨‍⚖ Открыть итог консультации",
    "consultation_result_open",
)
COMPLETED_M1_ACTION = (
    "📁 Итог завершённого дела",
    "my_case_open",
)


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


async def _result_view_for_case(db, case):
    consultation = await latest_case_consultation(db, case_id=case.id)
    if not is_terminal_consultation(consultation):
        return None
    return consultation_result_view(consultation)


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


async def _has_unsent_message_draft(state: FSMContext) -> bool:
    data = await state.get_data()
    return bool(str(data.get("draft_text") or "").strip())


def _draft_guard_markup():
    return one(
        ("↩️ Вернуться к черновику", "message_review_return"),
        ("✖️ Отменить черновик", "message_discard_confirm"),
    )


def _draft_guard_text() -> str:
    return (
        "📝 У вас есть неотправленный черновик вопроса.\n\n"
        "Я не закрываю его автоматически, чтобы введённый текст не потерялся. "
        "Вернитесь к черновику или отмените его явно — удаление потребует подтверждения."
    )


async def _guard_message_draft(message: Message, state: FSMContext) -> bool:
    if not await _has_unsent_message_draft(state):
        return False
    await message.answer(
        _draft_guard_text(),
        reply_markup=_draft_guard_markup(),
    )
    return True


async def _guard_callback_draft(callback: CallbackQuery, state: FSMContext) -> bool:
    if not await _has_unsent_message_draft(state):
        return False
    await _safe_callback_edit(
        callback,
        _draft_guard_text(),
        reply_markup=_draft_guard_markup(),
        unchanged_notice="Черновик сохранён и ждёт вашего решения.",
    )
    return True


async def _active_case_exists(db, message: Message) -> bool:
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    await db.commit()
    return case is not None


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
        result_view = await _result_view_for_case(db, case)
        shown_next_action = result_view.next_step if result_view else view.next_action
        primary_action = (
            CONSULTATION_RESULT_ACTION if result_view else _primary_action(view)
        )
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
            shown_next_action,
        ]
        if result_view:
            lines.extend(
                [
                    "",
                    f"👨‍⚖ {result_view.status_text}",
                    "Откройте итог консультации: там сохранён результат юриста и актуальное продолжение.",
                ]
            )
        if view.unread_team_messages:
            lines.extend(
                [
                    "",
                    f"💬 Новые ответы команды: {view.unread_team_messages}",
                    (
                        "Ответы доступны в переписке; итог консультации остаётся главным действием."
                        if result_view
                        else "Сначала откройте переписку: ответ может уточнять документы, сроки или дальнейшие действия."
                    ),
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
        return "\n".join(lines), True, primary_action

    completed_m1 = await latest_completed_m1_case_for_user(db, user_id=user.id)
    if completed_m1:
        view = await load_client_case_view(db, completed_m1)
        lines = [
            "🏠 Главная",
            "",
            "✅ Последнее дело завершено",
            f"📁 Дело № {view.case_number}",
            f"Услуга: {view.route_label}",
            "",
            "Итог",
            "Финальный платёж подтверждён, финансовый этап завершён и дело закрыто.",
            "",
            f"📄 Документы: {view.documents.summary}",
        ]
        if view.payments_summary:
            lines.append(f"💳 Оплаты: {view.payments_summary}")
        lines.extend(
            [
                "",
                f"Обновлено: {format_updated_at(view.updated_at)}",
                "Итог, история и платежи сохранены в режиме просмотра. Новое обращение можно начать отдельно.",
            ]
        )
        return "\n".join(lines), False, COMPLETED_M1_ACTION

    latest_result = await latest_terminal_client_consultation(
        db,
        client_id=user.id,
    )
    if latest_result:
        result_case, consultation = latest_result
        result_view = consultation_result_view(consultation)
        if result_view:
            text = (
                "🏠 Главная\n\n"
                "👨‍⚖ Итог последней консультации сохранён.\n"
                f"Дело № {result_case.case_number}\n"
                f"{result_view.status_text}\n\n"
                "Что дальше\n"
                f"{result_view.next_step}\n\n"
                "Полный результат юриста доступен по главной кнопке ниже."
            )
            return text, False, CONSULTATION_RESULT_ACTION

    text = (
        "🏠 Добро пожаловать\n\n"
        "Я помогу предварительно рассчитать неустойку по ДДУ, передать документы юристу "
        "и отслеживать ход дела прямо в Telegram.\n\n"
        "Расчёт предварительный и не является юридическим заключением."
    )
    return text, False, None


@router.message(lambda m: m.text in ["/start", "/menu", "🏠 Главная"])
async def start(message: Message, db, state: FSMContext):
    if await _guard_message_draft(message, state):
        return
    await state.clear()
    text, case_exists, primary_action = await _home_text(db, message)
    await db.commit()
    await message.answer(text, reply_markup=reply_main_menu(case_exists))
    await message.answer(
        "Выберите действие:",
        reply_markup=main_menu(
            case_exists,
            primary_action=primary_action,
        ),
    )


@router.message(lambda m: m.text == "🧮 Рассчитать неустойку")
async def menu_calc(message: Message, state: FSMContext, db):
    if await _guard_message_draft(message, state):
        return
    await state.clear()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        view = await load_client_case_view(db, case)
        result_view = await _result_view_for_case(db, case)
        primary_action = (
            CONSULTATION_RESULT_ACTION if result_view else _primary_action(view)
        )
        await db.commit()
        await message.answer(
            "📁 У вас уже есть активное дело.\n\n"
            "Чтобы не смешивать расчёты, документы и статусы разных обращений, "
            "сначала продолжите текущее дело. Новый расчёт станет доступен после "
            "его завершения. Нижнее меню уже обновлено.",
            reply_markup=reply_main_menu(True),
        )
        await message.answer(
            "Продолжите текущее дело:",
            reply_markup=main_menu(
                True,
                primary_action=primary_action,
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
    if await _guard_message_draft(message, state):
        return
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
    if await _guard_message_draft(message, state):
        return
    await state.clear()
    await message.answer(
        "В разделе документов видны актуальные файлы, замечания юриста и история версий.",
        reply_markup=one(
            ("📄 Открыть документы", "documents_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(lambda m: m.text == "💬 Переписка")
async def menu_messages(message: Message, state: FSMContext):
    if await _guard_message_draft(message, state):
        return
    await state.clear()
    await message.answer(
        "Откройте историю сообщений по текущему делу.",
        reply_markup=one(
            ("💬 Открыть переписку", "message_history"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(lambda m: m.text == "✉️ Новый вопрос")
async def menu_new_question(message: Message, state: FSMContext):
    if await _guard_message_draft(message, state):
        return
    await state.clear()
    await message.answer(
        "Сформулируйте новый вопрос в переписке по текущему делу.",
        reply_markup=one(
            ("✉️ Задать вопрос", "message_create"),
            ("💬 История сообщений", "message_history"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(lambda m: m.text == "💬 Связаться с юристом")
async def menu_lawyer(message: Message, state: FSMContext):
    if await _guard_message_draft(message, state):
        return
    await state.clear()
    await message.answer(
        "Выберите способ связи или вернитесь на главную.",
        reply_markup=one(
            ("💬 Открыть связь с юристом", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(lambda m: m.text == "/help")
async def help_command(message: Message, db):
    case_exists = await _active_case_exists(db, message)
    payment_line = (
        "Онлайн-оплата сейчас отключена; доступные этапы продолжаются без платёжной ссылки."
        if payments_disabled()
        else "Оплата доступна на соответствующих этапах дела."
    )
    await message.answer(
        "ℹ️ Помощь\n\n"
        "Основные разделы:\n"
        "🧮 Рассчитать неустойку — предварительный расчёт, когда активного дела нет.\n"
        "📁 Моё дело — текущий этап, готовность, ответы и следующее действие.\n"
        "📄 Документы — актуальные версии, замечания и история.\n"
        "💬 Переписка — сообщения по активному делу.\n"
        "✉️ Новый вопрос — новый вопрос команде по делу.\n\n"
        f"{payment_line}\n\n"
        "Команды: /start, /menu, /status, /help, /cancel",
        reply_markup=reply_main_menu(case_exists),
    )


@router.message(lambda m: m.text == "/status")
async def status_command(message: Message, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        completed_m1 = await latest_completed_m1_case_for_user(db, user_id=user.id)
        if completed_m1:
            view = await load_client_case_view(db, completed_m1)
            await db.commit()
            buttons: list[tuple[str, str]] = [
                COMPLETED_M1_ACTION,
                ("🕘 История дела", "case_history_open"),
            ]
            if not payments_disabled():
                buttons.append(("💳 Оплаты по делу", "payments_open"))
            buttons.append(("🏠 Главная", "nav_home"))
            await message.answer(
                "✅ Последнее дело завершено.\n\n"
                f"📁 Дело № {view.case_number}\n"
                "Финальный платёж подтверждён, финансовый этап завершён и дело закрыто.\n\n"
                "Действий по этому делу больше не требуется. Итог, история и платежи доступны только для просмотра.",
                reply_markup=reply_main_menu(False),
            )
            await message.answer(
                "Открыть архив дела:",
                reply_markup=one(*buttons),
            )
            return

        latest_result = await latest_terminal_client_consultation(
            db,
            client_id=user.id,
        )
        await db.commit()
        if latest_result:
            result_case, consultation = latest_result
            result_view = consultation_result_view(consultation)
            if result_view:
                await message.answer(
                    "Активных дел сейчас нет.\n\n"
                    "👨‍⚖ Итог последней консультации сохранён.\n"
                    f"Дело № {result_case.case_number}\n"
                    f"{result_view.status_text}\n\n"
                    f"Следующий шаг: {result_view.next_step}",
                    reply_markup=reply_main_menu(False),
                )
                await message.answer(
                    "Продолжить:",
                    reply_markup=one(
                        CONSULTATION_RESULT_ACTION,
                        ("🏠 Главная", "nav_home"),
                    ),
                )
                return
        await message.answer(
            "Активного дела пока нет. Нижнее меню показывает доступные варианты начала.",
            reply_markup=reply_main_menu(False),
        )
        return

    view = await load_client_case_view(db, case)
    result_view = await _result_view_for_case(db, case)
    await db.commit()
    shown_next_action = result_view.next_step if result_view else view.next_action
    lines = [
        f"📁 {view.case_number}",
        f"Услуга: {view.route_label}",
        f"Сейчас: {view.status_label}",
        f"Документы: {view.documents.summary}",
    ]
    if result_view:
        lines.append(f"👨‍⚖ {result_view.status_text}")
    if view.unread_team_messages:
        lines.append(f"💬 Новые ответы команды: {view.unread_team_messages}")
    lines.extend(["", f"Следующий шаг: {shown_next_action}"])
    buttons: list[tuple[str, str]] = []
    if result_view:
        buttons.append(CONSULTATION_RESULT_ACTION)
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
        reply_markup=reply_main_menu(True),
    )
    await message.answer(
        "Продолжить:",
        reply_markup=one(*buttons),
    )


@router.message(lambda m: m.text in ["/cancel", "Отмена"])
async def cancel_message(message: Message, state: FSMContext, db):
    if await _guard_message_draft(message, state):
        return
    await state.clear()
    case_exists = await _active_case_exists(db, message)
    await message.answer(
        "Действие отменено. Уже сохранённые данные не удалены.",
        reply_markup=reply_main_menu(case_exists),
    )


@router.callback_query(lambda c: c.data == "nav_home")
async def home(callback: CallbackQuery, db, state: FSMContext):
    if await _guard_callback_draft(callback, state):
        return
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
    if await _guard_callback_draft(callback, state):
        return
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
    if await _guard_callback_draft(callback, state):
        return
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
    if await _guard_callback_draft(callback, state):
        return
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