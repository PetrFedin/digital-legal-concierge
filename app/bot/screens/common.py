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
from app.bot.payment_presentation import offline_m1_payment_presentation
from app.domain.cases.client_case_scope import latest_completed_case_for_user
from app.domain.payments.mode import payments_disabled

router = Router()


# Retained as a public compatibility contract for integrations that inspect
# no-payment pilot wording. Actual client screens use the shared case view plus
# the provider-aware payment presentation below.
PILOT_NEXT_ACTIONS = {
    "M1_WAITING_PAYMENT_30000": "Первый платёж ожидает подтверждения командой",
    "M1_WAITING_PAYMENT_70000": "Второй платёж ожидает подтверждения командой",
    "M1_WAITING_SUCCESS_FEE": "Финальный платёж ожидает подтверждения командой",
    "M2_PAYMENT_PENDING": "Подтвердить запись на консультацию",
}

CONSULTATION_RESULT_ACTION = (
    "👨‍⚖ Открыть итог консультации",
    "consultation_result_open",
)
COMPLETED_CASE_ACTION = (
    "📁 Открыть архив обращения",
    "my_case_open",
)
CASE_SELECTION_ACTION = (
    "📁 Выбрать обращение",
    "my_cases_open",
)


def _route_label(route: str | None) -> str:
    return route_label(route)


def _next_action(case) -> str:
    if payments_disabled() and str(case.status) in PILOT_NEXT_ACTIONS:
        return PILOT_NEXT_ACTIONS[str(case.status)]
    return next_action_text(case)


def _shown_next_action(view) -> str:
    offline_payment = offline_m1_payment_presentation(view)
    return offline_payment.next_action if offline_payment else view.next_action


def _primary_action(view) -> tuple[str, str]:
    offline_payment = offline_m1_payment_presentation(view)
    if offline_payment:
        return (offline_payment.button_label, offline_payment.callback)
    if view.action:
        if view.action.callback == "my_case_open":
            return ("🔄 Обновить статус", "my_case_open")
        return (
            f"▶️ {view.action.label}",
            f"next_action:v2:{view.case_id}:{view.action_key}",
        )
    if view.unread_team_messages:
        return (
            f"💬 Прочитать ответ команды ({view.unread_team_messages})",
            "message_history",
        )
    return ("📁 Открыть текущее дело", "my_case_open")


def _selection_required_text(active_count: int) -> str:
    return (
        "🏠 Главная\n\n"
        "📁 Выберите обращение\n"
        "У вас несколько активных обращений, а текущее дело не выбрано. "
        "Чтобы документы, оплаты, переписка и действия не смешались между делами, "
        "бот ничего не выбирает автоматически.\n\n"
        f"Активных обращений: {active_count}\n\n"
        "📌 Ваш следующий шаг\n"
        "Откройте список обращений и выберите нужное. Новый расчёт при этом остаётся доступен отдельно."
    )


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


async def _client_case_menu_state(db, message: Message) -> tuple[bool, bool]:
    """Return (active_case, completed_archive) for the persistent reply menu.

    Several active matters still count as an active client context even if none
    is currently selected. The menu must not visually downgrade such a client to
    a completed archive merely because ambiguity is being handled fail-closed.
    """

    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case is not None:
        await db.commit()
        return True, False
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    if active_cases:
        await db.commit()
        return True, False
    completed = await latest_completed_case_for_user(db, user_id=user.id)
    await db.commit()
    return False, completed is not None


async def _home_text(
    db,
    message_or_callback,
) -> tuple[str, bool, bool, tuple[str, str] | None]:
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
        shown_next_action = (
            result_view.next_step if result_view else _shown_next_action(view)
        )
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
            view.now_text,
            "",
            "Требуется от вас",
            view.client_requirement,
        ]
        if view.blocker:
            lines.extend(
                [
                    "",
                    f"⚠️ Что мешает продолжить: {view.blocker}",
                ]
            )
        lines.extend(
            [
                "",
                "📌 Ваш следующий шаг",
                shown_next_action,
            ]
        )
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
                        else "Ответ доступен в переписке и не меняет процессный этап сам по себе."
                    ),
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
        lines.append(f"🕘 История: {view.history_summary}")
        lines.extend(
            [
                "",
                f"Обновлено: {format_updated_at(view.updated_at)}",
                "Главная кнопка ниже ведёт к самому актуальному действию.",
            ]
        )
        return "\n".join(lines), True, False, primary_action

    # The selected Case can legitimately disappear from the active scope after
    # it closes while other matters remain. Fail closed, but make that state
    # visible and actionable instead of showing an unrelated completed archive.
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    if active_cases:
        return (
            _selection_required_text(len(active_cases)),
            True,
            False,
            CASE_SELECTION_ACTION,
        )

    completed_case = await latest_completed_case_for_user(db, user_id=user.id)
    if completed_case:
        view = await load_client_case_view(db, completed_case)
        result_view = await _result_view_for_case(db, completed_case)
        is_m2 = str(view.route or "") == "M2"
        if is_m2:
            lines = [
                "🏠 Главная",
                "",
                "✅ Последняя консультация завершена",
                f"📁 Обращение № {view.case_number}",
                f"Услуга: {view.route_label}",
                "",
                "Итог",
                (
                    result_view.status_text
                    if result_view
                    else "Консультационный маршрут завершён."
                ),
                "",
                "Что дальше",
                "Действий по закрытому обращению больше не требуется. Итог, документы, платежи и история сохранены в архиве.",
                "",
                f"📄 Документы: {view.documents.summary}",
                "💳 Оплаты: сохранены в архиве обращения",
                "",
                f"Обновлено: {format_updated_at(view.updated_at)}",
                "Новое обращение можно начать отдельно; архив текущего останется только для просмотра.",
            ]
            primary_action = (
                CONSULTATION_RESULT_ACTION if result_view else COMPLETED_CASE_ACTION
            )
        else:
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
                "💳 Оплаты: сохранены в архиве дела",
                "",
                f"Обновлено: {format_updated_at(view.updated_at)}",
                "Итог, история и платежи сохранены в режиме просмотра. Новое обращение можно начать отдельно.",
            ]
            primary_action = COMPLETED_CASE_ACTION
        return "\n".join(lines), False, True, primary_action

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
            return text, False, False, CONSULTATION_RESULT_ACTION

    text = (
        "🏠 Добро пожаловать\n\n"
        "Я помогу предварительно рассчитать неустойку по ДДУ, передать документы юристу "
        "и отслеживать ход дела прямо в Telegram.\n\n"
        "Расчёт предварительный и не является юридическим заключением."
    )
    return text, False, False, None


@router.message(lambda m: m.text in ["/start", "/menu", "🏠 Главная"])
async def start(message: Message, db, state: FSMContext):
    if await _guard_message_draft(message, state):
        return
    await state.clear()
    text, case_exists, completed_case, primary_action = await _home_text(db, message)
    await db.commit()
    await message.answer(
        text,
        reply_markup=reply_main_menu(
            case_exists,
            completed_case=completed_case,
        ),
    )
    await message.answer(
        "Выберите действие:",
        reply_markup=main_menu(
            case_exists,
            completed_case=completed_case,
            primary_action=primary_action,
        ),
    )


@router.message(lambda m: m.text == "🧮 Рассчитать неустойку")
async def menu_calc(message: Message, state: FSMContext, db):
    """Compatibility handler: calculator remains available with active Cases.

    reply_menu_direct owns the canonical persistent-menu path and is registered
    before this router. Keeping this historical handler behavior-identical makes
    correctness independent from router order while old deployments/messages are
    still being retired.
    """

    if await _guard_message_draft(message, state):
        return
    await state.clear()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    await db.commit()
    if active_cases:
        await message.answer(
            "🧮 НОВЫЙ РАСЧЁТ\n\n"
            "Расчёт доступен независимо от уже открытых дел. Если вы продолжите, будет создано отдельное обращение; существующие M1/M2 дела, документы и статусы не изменятся.\n\n"
            f"Сейчас активных обращений: {len(active_cases)}.",
            reply_markup=one(
                ("▶️ Начать новый расчёт", "calc_start"),
                ("📁 Выбрать текущее дело", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await message.answer(
        "🧮 ПРЕДВАРИТЕЛЬНЫЙ РАСЧЁТ\n\n"
        "Ответьте на несколько вопросов о ДДУ. Расчёт предварительный и не является юридическим заключением.",
        reply_markup=one(
            ("▶️ Начать расчёт", "calc_start"),
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
    case_exists, completed_case = await _client_case_menu_state(db, message)
    payment_line = (
        "Онлайн-оплата сейчас отключена; обязательства и их статус доступны в разделе «Оплаты», а финансовый этап подтверждает команда после проверки фактического поступления."
        if payments_disabled()
        else "Оплата доступна на соответствующих этапах дела."
    )
    await message.answer(
        "ℹ️ Помощь\n\n"
        "Основные разделы:\n"
        "🏠 Главная — выбранное обращение и главный следующий шаг.\n"
        "🧮 Рассчитать неустойку — новый предварительный расчёт; доступен всегда и создаёт отдельное обращение, не изменяя уже открытые дела.\n"
        "📁 Моё дело — выбранное активное обращение; если их несколько, бот предлагает выбрать нужное.\n"
        "📄 Документы — актуальные версии, замечания и история именно выбранного обращения.\n"
        "💬 Связаться с юристом — переписка по выбранному M1/M2 или продолжение сохранённой консультации.\n\n"
        f"{payment_line}\n\n"
        "Команды: /start, /menu, /status, /help, /cancel",
        reply_markup=reply_main_menu(
            case_exists,
            completed_case=completed_case,
        ),
    )


@router.message(lambda m: m.text == "/status")
async def status_command(message: Message, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
        if active_cases:
            await db.commit()
            await message.answer(
                "📁 Статус какого обращения показать?\n\n"
                f"У вас {len(active_cases)} активных обращений. Текущее дело не выбрано, поэтому бот не смешивает статусы разных дел.",
                reply_markup=reply_main_menu(True),
            )
            await message.answer(
                "Выберите обращение:",
                reply_markup=one(
                    CASE_SELECTION_ACTION,
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return

        completed_case = await latest_completed_case_for_user(db, user_id=user.id)
        if completed_case:
            view = await load_client_case_view(db, completed_case)
            result_view = await _result_view_for_case(db, completed_case)
            await db.commit()
            is_m2 = str(view.route or "") == "M2"
            buttons: list[tuple[str, str]] = []
            if is_m2 and result_view:
                buttons.append(CONSULTATION_RESULT_ACTION)
            buttons.extend(
                [
                    COMPLETED_CASE_ACTION,
                    ("💳 Оплаты по обращению", "payments_open"),
                    ("🕘 История обращения", "case_history_open"),
                    ("🏠 Главная", "nav_home"),
                ]
            )
            if is_m2:
                text = (
                    "✅ Последняя консультация завершена.\n\n"
                    f"📁 Обращение № {view.case_number}\n"
                    + (
                        f"{result_view.status_text}\n\n"
                        if result_view
                        else "Консультационный маршрут завершён.\n\n"
                    )
                    + "Действий по закрытому обращению больше не требуется. Итог, документы, платежи и история доступны только для просмотра."
                )
            else:
                text = (
                    "✅ Последнее дело завершено.\n\n"
                    f"📁 Дело № {view.case_number}\n"
                    "Финальный платёж подтверждён, финансовый этап завершён и дело закрыто.\n\n"
                    "Действий по этому делу больше не требуется. Итог, история и платежи доступны только для просмотра."
                )
            await message.answer(
                text,
                reply_markup=reply_main_menu(False, completed_case=True),
            )
            await message.answer(
                "Открыть архив обращения:",
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
    shown_next_action = (
        result_view.next_step if result_view else _shown_next_action(view)
    )
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
    if not result_view and not view.unread_team_messages:
        primary_action = _primary_action(view)
        if primary_action[1] != "my_case_open":
            buttons.append(primary_action)
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
    case_exists, completed_case = await _client_case_menu_state(db, message)
    await message.answer(
        "Действие отменено. Уже сохранённые данные не удалены.",
        reply_markup=reply_main_menu(
            case_exists,
            completed_case=completed_case,
        ),
    )


@router.callback_query(lambda c: c.data == "nav_home")
async def home(callback: CallbackQuery, db, state: FSMContext):
    if await _guard_callback_draft(callback, state):
        return
    await state.clear()
    text, case_exists, completed_case, primary_action = await _home_text(db, callback)
    await _safe_callback_edit(
        callback,
        text,
        reply_markup=main_menu(
            case_exists,
            completed_case=completed_case,
            primary_action=primary_action,
        ),
    )


@router.callback_query(lambda c: c.data == "noop")
async def noop(callback: CallbackQuery, db, state: FSMContext):
    if await _guard_callback_draft(callback, state):
        return
    await state.clear()
    text, case_exists, completed_case, primary_action = await _home_text(db, callback)
    await _safe_callback_edit(
        callback,
        "Эта кнопка больше не актуальна. Показано текущее состояние.\n\n" + text,
        reply_markup=main_menu(
            case_exists,
            completed_case=completed_case,
            primary_action=primary_action,
        ),
        unchanged_notice="Показано текущее состояние.",
    )


@router.callback_query(lambda c: c.data == "nav_cancel")
async def cancel(callback: CallbackQuery, state: FSMContext, db):
    if await _guard_callback_draft(callback, state):
        return
    await state.clear()
    text, case_exists, completed_case, primary_action = await _home_text(db, callback)
    await _safe_callback_edit(
        callback,
        "Действие отменено. Уже сохранённые данные не удалены.\n\n" + text,
        reply_markup=main_menu(
            case_exists,
            completed_case=completed_case,
            primary_action=primary_action,
        ),
        unchanged_notice="Действие уже отменено.",
    )


@router.callback_query(lambda c: c.data == "nav_back")
async def back(callback: CallbackQuery, state: FSMContext, db):
    if await _guard_callback_draft(callback, state):
        return
    await state.clear()
    text, case_exists, completed_case, primary_action = await _home_text(db, callback)
    await _safe_callback_edit(
        callback,
        text,
        reply_markup=main_menu(
            case_exists,
            completed_case=completed_case,
            primary_action=primary_action,
        ),
    )
