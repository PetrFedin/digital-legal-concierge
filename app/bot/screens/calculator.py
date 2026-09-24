import logging
from datetime import date, datetime
from decimal import Decimal

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.calculator_draft import (
    CALCULATOR_CASE_ID,
    clear_calculator_draft_metadata,
    draft_step,
    draft_step_label,
    finish_calculator_case,
    has_saved_calculator_draft,
    mark_calculator_draft_paused,
    start_fresh_calculator_case,
)
from app.bot.case_callback_scope import bound_case_callback
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import CalculatorStates
from app.domain.calculator.calculator_result_formatter import (
    format_calculation_details,
    format_calculation_result,
)
from app.domain.calculator.calculator_service import (
    CalculatorRouteEligibilityError,
    CalculatorService,
)
from app.domain.calculator.penalty_calculator import parse_money
from app.domain.calculator.rule_engine import (
    CalculationManualReviewRequired,
    CalculationRuleError,
)
from app.domain.statuses.case_statuses import CaseStatus

logger = logging.getLogger(__name__)
router = Router()

_LEGACY_UNBOUND_CALCULATOR_ACTIONS = frozenset(
    {
        "calc_resume",
        "calc_restart_confirm",
        "calc_restart",
        "calc_back_price",
        "calc_back_planned",
        "calc_back_transfer_status",
        "calc_object_transferred_yes",
        "calc_object_transferred_no",
        "calc_future_date_consult",
    }
)


def _price_prompt(current: str | None = None) -> str:
    current_note = f"\nСейчас сохранено: {current} ₽. Введите новую сумму." if current else ""
    return (
        "🧮 Расчёт неустойки · шаг 1 из 6\n\n"
        "Ответьте на несколько вопросов. Расчёт будет предварительным и не является юридическим заключением.\n\n"
        "💰 Введите стоимость объекта по ДДУ в рублях.\n"
        f"Например: 8500000{current_note}"
    )


def _planned_prompt(current: str | None = None) -> str:
    current_note = f"\nСейчас сохранена дата: {current}." if current else ""
    return (
        "🧮 Расчёт неустойки · шаг 2 из 6\n\n"
        "📅 Укажите дату передачи объекта по ДДУ. Формат ДД.ММ.ГГГГ."
        f"{current_note}"
    )


def _future_date_prompt(planned_date: date) -> str:
    return (
        "ℹ️ Срок передачи по ДДУ ещё не наступил.\n\n"
        f"Сохранённая дата передачи: {planned_date.strftime('%d.%m.%Y')}.\n"
        "Автоматический расчёт просрочки сейчас не выполняется. "
        "Вы можете сохранить этот сценарий и вернуться после наступления срока, "
        "изменить дату или перейти к личной консультации."
    )


def _transfer_prompt() -> str:
    return "🧮 Расчёт неустойки · шаг 3 из 6\n\n🏗 Объект уже передан по акту?"


def _actual_prompt() -> str:
    return (
        "🧮 Расчёт неустойки · шаг 4 из 6\n\n"
        "📅 Укажите дату фактической передачи по акту. Формат ДД.ММ.ГГГГ"
    )


def _client_type_prompt() -> str:
    return (
        "🧮 Расчёт неустойки · шаг 5 из 6\n\n"
        "👤 Уточните статус участника ДДУ.\n\n"
        "Вы являетесь гражданином и заключали ДДУ для личных, семейных, "
        "домашних или иных нужд, не связанных с предпринимательской деятельностью?"
    )


def _unique_object_prompt() -> str:
    return (
        "🧮 Расчёт неустойки · шаг 6 из 6\n\n"
        "🏢 Отнесён ли дом или иной объект недвижимости по проектной документации "
        "к уникальным объектам?\n\n"
        "Если вы не уверены, автоматический расчёт не будет угадывать этот факт — "
        "мы передадим вопрос юристу."
    )


def _price_keyboard(case_id: int):
    return one(
        ("Не знаю стоимость", bound_case_callback("calc_unknown_price", case_id)),
        ("💾 Сохранить и выйти", "nav_home"),
    )


def _planned_keyboard(case_id: int):
    return one(
        ("Не знаю дату", bound_case_callback("calc_unknown_date", case_id)),
        ("⬅️ Изменить стоимость", bound_case_callback("calc_back_price", case_id)),
        ("💾 Сохранить и выйти", "nav_home"),
    )


def _future_date_keyboard(case_id: int):
    return one(
        (
            "💬 Перейти к консультации",
            bound_case_callback("calc_future_date_consult", case_id),
        ),
        (
            "✏️ Изменить дату по ДДУ",
            bound_case_callback("calc_back_planned", case_id),
        ),
        ("💾 Сохранить и выйти", "nav_home"),
    )


def _transfer_keyboard(case_id: int):
    return one(
        (
            "Да, передан",
            bound_case_callback("calc_object_transferred_yes", case_id),
        ),
        (
            "Нет, не передан",
            bound_case_callback("calc_object_transferred_no", case_id),
        ),
        (
            "⬅️ Изменить дату по ДДУ",
            bound_case_callback("calc_back_planned", case_id),
        ),
        ("💾 Сохранить и выйти", "nav_home"),
    )


def _actual_keyboard(case_id: int):
    return one(
        (
            "⬅️ Назад к статусу передачи",
            bound_case_callback("calc_back_transfer_status", case_id),
        ),
        ("💾 Сохранить и выйти", "nav_home"),
    )


def _client_type_keyboard(case_id: int):
    return one(
        (
            "Да, гражданин для личных нужд",
            bound_case_callback("calc_client_consumer", case_id),
        ),
        (
            "Нет, иной участник",
            bound_case_callback("calc_client_other", case_id),
        ),
        (
            "Не уверен — нужна проверка",
            bound_case_callback("calc_client_unknown", case_id),
        ),
        ("💾 Сохранить и выйти", "nav_home"),
    )


def _unique_object_keyboard(case_id: int):
    return one(
        ("Да, уникальный объект", bound_case_callback("calc_unique_yes", case_id)),
        ("Нет", bound_case_callback("calc_unique_no", case_id)),
        (
            "Не знаю — проверить документы",
            bound_case_callback("calc_unique_unknown", case_id),
        ),
        ("💾 Сохранить и выйти", "nav_home"),
    )


def _result_recovery_keyboard():
    return one(
        ("🧮 Новый расчёт", "calc_start"),
        ("📁 Моё дело", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    )


def _draft_summary(data: dict) -> str:
    rows: list[str] = []
    if data.get("contract_price"):
        rows.append(f"💰 Стоимость: {data['contract_price']} ₽")
    if data.get("planned_transfer_date"):
        try:
            shown = date.fromisoformat(str(data["planned_transfer_date"])).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            shown = str(data["planned_transfer_date"])
        rows.append(f"📅 Дата по ДДУ: {shown}")
    if "object_transferred" in data:
        rows.append(
            "🏗 Объект передан: " + ("да" if bool(data.get("object_transferred")) else "нет")
        )
    if data.get("actual_transfer_date"):
        try:
            shown_actual = date.fromisoformat(str(data["actual_transfer_date"])).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            shown_actual = str(data["actual_transfer_date"])
        rows.append(f"🗓 Фактическая передача: {shown_actual}")
    if str(data.get("client_type") or "") in {"consumer", "other"}:
        rows.append(
            "👤 Тип участника: "
            + (
                "гражданин для личных нужд"
                if data["client_type"] == "consumer"
                else "иной участник"
            )
        )
    if "unique_object" in data:
        rows.append(
            "🏢 Уникальный объект: "
            + ("да" if bool(data.get("unique_object")) else "нет")
        )
    return "\n".join(rows) or "Расчёт начат, ответы пока не введены."


def _parse_case_callback(data: str | None, prefix: str) -> int:
    marker = f"{prefix}:v2:"
    raw = str(data or "")
    if not raw.startswith(marker):
        raise ValueError("Устаревшая кнопка")
    case_id = int(raw[len(marker) :])
    if case_id <= 0:
        raise ValueError("Некорректное обращение")
    return case_id


def _current_case_id(data: dict) -> int:
    try:
        return int(data.get(CALCULATOR_CASE_ID) or 0)
    except (TypeError, ValueError):
        return 0


def _require_current_case_callback(
    data: str | None,
    *,
    prefix: str,
    state_data: dict,
) -> int:
    expected_case_id = _parse_case_callback(data, prefix)
    current_case_id = _current_case_id(state_data)
    if current_case_id <= 0 or current_case_id != expected_case_id:
        raise ValueError("Кнопка относится к другому обращению")
    return expected_case_id


async def _present_committed_callback(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup,
    saved_notice: str,
) -> None:
    """Present an already committed result without converting UI failure into retry."""
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            try:
                await callback.answer(saved_notice)
            except Exception:
                logger.warning("Committed calculator callback acknowledgement failed")
            return
        logger.warning("Committed calculator result edit failed: %s", error)
    except Exception:
        logger.exception("Committed calculator result edit failed")

    try:
        await callback.message.answer(text, reply_markup=reply_markup)
        try:
            await callback.answer("Изменение сохранено. Результат открыт новым сообщением.")
        except Exception:
            logger.warning("Committed calculator fallback acknowledgement failed")
    except Exception:
        logger.exception("Committed calculator fallback message failed")
        try:
            await callback.answer(saved_notice, show_alert=True)
        except Exception:
            logger.warning("Committed calculator final acknowledgement failed")


async def _recover_stale_step(callback: CallbackQuery, state: FSMContext) -> None:
    # A stale Telegram button must not erase another Case's paused calculator
    # draft. Pause the current working set when possible and only drop the active
    # FSM state, leaving per-Case draft data intact.
    paused = await mark_calculator_draft_paused(state)
    if not paused:
        await state.set_state(None)
    await callback.message.edit_text(
        "Этот шаг расчёта больше не актуален или относится к другому обращению. Данные не изменены.\n\n"
        "Откройте нужное дело или начните новый расчёт.",
        reply_markup=_result_recovery_keyboard(),
    )


def _base_data_ready(data: dict) -> bool:
    return bool(data.get("contract_price") and data.get("planned_transfer_date"))


async def _start_fresh(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    case_id: int,
) -> None:
    """Reset one Case questionnaire while preserving other Case drafts."""
    await start_fresh_calculator_case(state, case_id=int(case_id))
    await state.set_state(CalculatorStates.waiting_contract_price)
    await callback.message.edit_text(
        _price_prompt(),
        reply_markup=_price_keyboard(int(case_id)),
    )


async def _resume_draft(callback: CallbackQuery, state: FSMContext) -> None:
    data = await clear_calculator_draft_metadata(state)
    case_id = _current_case_id(data)
    if case_id <= 0:
        await _recover_stale_step(callback, state)
        return
    step = draft_step(data)
    if step == "price":
        await state.set_state(CalculatorStates.waiting_contract_price)
        await callback.message.edit_text(
            _price_prompt(str(data.get("contract_price") or "") or None),
            reply_markup=_price_keyboard(case_id),
        )
        return
    if step == "planned_date":
        current = None
        if data.get("planned_transfer_date"):
            try:
                current = date.fromisoformat(str(data["planned_transfer_date"])).strftime("%d.%m.%Y")
            except (TypeError, ValueError):
                current = None
        await state.set_state(CalculatorStates.waiting_planned_transfer_date)
        await callback.message.edit_text(
            _planned_prompt(current),
            reply_markup=_planned_keyboard(case_id),
        )
        return
    if step == "future_date":
        try:
            planned_date = date.fromisoformat(str(data["planned_transfer_date"]))
        except (TypeError, ValueError, KeyError):
            await state.set_state(CalculatorStates.waiting_planned_transfer_date)
            await callback.message.edit_text(
                _planned_prompt(),
                reply_markup=_planned_keyboard(case_id),
            )
            return
        await state.set_state(CalculatorStates.waiting_planned_transfer_date)
        await callback.message.edit_text(
            _future_date_prompt(planned_date),
            reply_markup=_future_date_keyboard(case_id),
        )
        return
    if step == "transfer_status":
        if not _base_data_ready(data):
            await _start_fresh(callback, state, case_id=case_id)
            return
        await state.set_state(CalculatorStates.waiting_object_transfer_status)
        await callback.message.edit_text(
            _transfer_prompt(),
            reply_markup=_transfer_keyboard(case_id),
        )
        return
    if not _base_data_ready(data):
        await _start_fresh(callback, state, case_id=case_id)
        return
    if step == "actual_date":
        await state.set_state(CalculatorStates.waiting_actual_transfer_date)
        await callback.message.edit_text(
            _actual_prompt(),
            reply_markup=_actual_keyboard(case_id),
        )
        return
    if step == "client_type":
        await state.set_state(CalculatorStates.waiting_client_type)
        await callback.message.edit_text(
            _client_type_prompt(),
            reply_markup=_client_type_keyboard(case_id),
        )
        return
    await state.set_state(CalculatorStates.waiting_unique_object)
    await callback.message.edit_text(
        _unique_object_prompt(),
        reply_markup=_unique_object_keyboard(case_id),
    )


async def _bound_case(ctx: BotContextService, user, state: FSMContext):
    data = await state.get_data()
    case_id = _current_case_id(data)
    if case_id <= 0:
        raise LookupError("Расчёт не привязан к обращению")
    case = await ctx.case_service.get_case_for_user(
        user_id=int(user.id),
        case_id=case_id,
    )
    if case is None:
        raise LookupError("Обращение расчёта не найдено")

    selected = await ctx.case_service.get_selected_case_for_user(
        int(user.id),
        include_terminal=False,
    )
    if selected is None:
        active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
        if len(active_cases) != 1 or int(active_cases[0].id) != case_id:
            raise LookupError("Перед действием нужно выбрать это обращение")
    elif int(selected.id) != case_id:
        raise LookupError("Сейчас выбрано другое обращение")
    return case


async def _show_saved_draft(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    case_id = _current_case_id(data)
    if case_id <= 0:
        await _recover_stale_step(callback, state)
        return
    await state.set_state(None)
    await callback.message.edit_text(
        "📝 Сохранён незавершённый расчёт\n\n"
        f"{_draft_summary(data)}\n\n"
        f"Следующий шаг: {draft_step_label(data)}.\n"
        "Продолжите с сохранённого места или начните заново — сброс потребует подтверждения.",
        reply_markup=one(
            (
                "▶️ Продолжить расчёт",
                bound_case_callback("calc_resume", case_id),
            ),
            (
                "Начать заново",
                bound_case_callback("calc_restart_confirm", case_id),
            ),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "calc_start")
async def calc_start(callback: CallbackQuery, state: FSMContext, db):
    """Start a genuinely new calculation Case for this source callback.

    Recovery of an unfinished existing Case is deliberately owned by
    ``calc_recover:v2:<case_id>``. The global calc_start token must therefore
    never be interpreted as implicit recovery merely because FSM still contains
    another Case id. Exact Telegram redelivery remains idempotent: if this same
    callback already created the Case currently bound in FSM, we resume that
    exact Case instead of resetting its answers.
    """

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    data = await state.get_data()
    previous_case_id = _current_case_id(data)

    try:
        case = await ctx.create_case_from_callback(
            user=user,
            callback=callback,
            purpose="calculator_start",
            status=CaseStatus.CALCULATOR_STARTED,
            title="Обращение по ДДУ",
        )
        case_id = int(case.id)
        case_status = str(case.status)
        selected_case = await ctx.case_service.get_selected_case_for_user(
            int(user.id),
            include_terminal=True,
        )
        selected_case_id = int(selected_case.id) if selected_case is not None else None
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Calculator Case creation failed")
        await callback.message.edit_text(
            "⚠️ Не удалось начать расчёт. Новое обращение не создано. Повторите действие.",
            reply_markup=one(
                ("🔄 Повторить", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if case_status not in {
        CaseStatus.NEW.value,
        CaseStatus.CALCULATOR_STARTED.value,
    }:
        # A delayed duplicate of an already completed start callback must never
        # reset either that progressed legal matter or a different current FSM
        # draft the client may now be editing.
        if previous_case_id == case_id:
            await finish_calculator_case(state, case_id=case_id)
        await callback.message.edit_text(
            "Этот запуск расчёта уже был обработан. Откройте дело, чтобы увидеть текущий этап.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🧮 Новый расчёт", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if selected_case_id != case_id:
        # Fresh creation always selects its new Case in the same transaction.
        # Therefore an active Case returned without becoming selected is a
        # delayed replay of an older source operation after the client switched
        # context. Do not let that old delivery steal the current FSM draft.
        await callback.message.edit_text(
            "Этот запуск расчёта уже обрабатывался для другого обращения. "
            "Текущее выбранное дело и незавершённый расчёт не изменены.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🧮 Новый расчёт", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if previous_case_id == case_id:
        # Exact redelivery/retry of the same source callback while that Case is
        # still current. Preserve any answers already collected for this Case.
        if has_saved_calculator_draft(data):
            await _show_saved_draft(callback, state)
        else:
            await _resume_draft(callback, state)
        return

    # Different callback id = explicit new operation/new Case. Existing Cases
    # and their paused calculator drafts remain intact; this flat FSM working set
    # now belongs to the new matter.
    await _start_fresh(callback, state, case_id=case_id)


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_resume:v2:")
)
async def resume_saved_calculation(callback: CallbackQuery, state: FSMContext, db):
    state_data = await state.get_data()
    try:
        _require_current_case_callback(
            callback.data,
            prefix="calc_resume",
            state_data=state_data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case = await _bound_case(ctx, user, state)
        await ctx.case_service.select_case_for_user(
            user_id=int(user.id),
            case_id=int(case.id),
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Saved calculator draft could not resolve its Case")
        await _recover_stale_step(callback, state)
        return
    await _resume_draft(callback, state)


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_restart_confirm:v2:")
)
async def confirm_restart_calculation(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        case_id = _require_current_case_callback(
            callback.data,
            prefix="calc_restart_confirm",
            state_data=data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return
    await state.set_state(None)
    await callback.message.edit_text(
        "Начать расчёт заново?\n\n"
        "Сохранённые ответы текущего незавершённого расчёта будут удалены. "
        "Само обращение и уже завершённые расчёты останутся в истории.",
        reply_markup=one(
            (
                "Да, удалить черновик",
                bound_case_callback("calc_restart", case_id),
            ),
            (
                "↩️ Вернуться к черновику",
                bound_case_callback("calc_resume", case_id),
            ),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_restart:v2:")
)
async def restart_calculation(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        case_id = _require_current_case_callback(
            callback.data,
            prefix="calc_restart",
            state_data=data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return
    await _start_fresh(callback, state, case_id=case_id)


@router.callback_query(lambda c: str(c.data or "").startswith("calc_repeat:v2:"))
async def repeat_calculation(callback: CallbackQuery, state: FSMContext, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case_id = _parse_case_callback(callback.data, "calc_repeat")
        case = await ctx.case_service.select_case_for_user(
            user_id=int(user.id),
            case_id=case_id,
        )
        selected_id = int(case.id)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Case-bound recalculation could not start")
        await callback.message.edit_text(
            "Эта кнопка расчёта больше не актуальна. Откройте нужное дело или начните новый расчёт.",
            reply_markup=_result_recovery_keyboard(),
        )
        return
    await _start_fresh(callback, state, case_id=selected_id)


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_back_price:v2:")
)
async def back_price(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        case_id = _require_current_case_callback(
            callback.data,
            prefix="calc_back_price",
            state_data=data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return
    await state.set_state(CalculatorStates.waiting_contract_price)
    await callback.message.edit_text(
        _price_prompt(str(data.get("contract_price") or "") or None),
        reply_markup=_price_keyboard(case_id),
    )


@router.message(CalculatorStates.waiting_contract_price)
async def price(message: Message, state: FSMContext):
    data = await state.get_data()
    case_id = _current_case_id(data)
    if case_id <= 0:
        await state.set_state(None)
        await message.answer(
            "Расчёт потерял связь с обращением. Начните новый расчёт с главной.",
        )
        return
    try:
        amount = parse_money(message.text)
    except Exception:
        await message.answer(
            "⚠️ Введите сумму цифрами. Например: 8500000. Ранее введённые данные не изменены.",
            reply_markup=one(("💾 Сохранить и выйти", "nav_home")),
        )
        return
    await state.update_data(contract_price=str(amount))
    await state.set_state(CalculatorStates.waiting_planned_transfer_date)
    await message.answer(
        _planned_prompt(),
        reply_markup=_planned_keyboard(case_id),
    )


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_back_planned:v2:")
)
async def back_planned(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        case_id = _require_current_case_callback(
            callback.data,
            prefix="calc_back_planned",
            state_data=data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return
    if not data.get("contract_price"):
        await _recover_stale_step(callback, state)
        return
    current = None
    if data.get("planned_transfer_date"):
        try:
            current = date.fromisoformat(data["planned_transfer_date"]).strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            current = None
    await state.set_state(CalculatorStates.waiting_planned_transfer_date)
    await callback.message.edit_text(
        _planned_prompt(current),
        reply_markup=_planned_keyboard(case_id),
    )


@router.message(CalculatorStates.waiting_planned_transfer_date)
async def planned(message: Message, state: FSMContext):
    data = await state.get_data()
    case_id = _current_case_id(data)
    if case_id <= 0:
        await state.set_state(None)
        await message.answer("Расчёт потерял связь с обращением. Начните новый расчёт с главной.")
        return
    try:
        planned_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except Exception:
        await message.answer(
            "⚠️ Дата нужна в формате ДД.ММ.ГГГГ. Ранее введённая стоимость сохранена.",
            reply_markup=_planned_keyboard(case_id),
        )
        return

    # Persist the accepted contractual date before any informational exit. If
    # the client corrected an upstream date, downstream transfer answers are no
    # longer safe to reuse implicitly and are intentionally cleared.
    updated_data = dict(data)
    updated_data["planned_transfer_date"] = planned_date.isoformat()
    updated_data.pop("object_transferred", None)
    updated_data.pop("actual_transfer_date", None)
    await state.set_data(updated_data)

    if planned_date > date.today():
        await state.set_state(CalculatorStates.waiting_planned_transfer_date)
        await message.answer(
            _future_date_prompt(planned_date),
            reply_markup=_future_date_keyboard(case_id),
        )
        return

    await state.set_state(CalculatorStates.waiting_object_transfer_status)
    await message.answer(
        _transfer_prompt(),
        reply_markup=_transfer_keyboard(case_id),
    )


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_back_transfer_status:v2:")
)
async def back_transfer_status(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        case_id = _require_current_case_callback(
            callback.data,
            prefix="calc_back_transfer_status",
            state_data=data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return
    if not _base_data_ready(data):
        await _recover_stale_step(callback, state)
        return
    await state.set_state(CalculatorStates.waiting_object_transfer_status)
    await callback.message.edit_text(
        _transfer_prompt(),
        reply_markup=_transfer_keyboard(case_id),
    )


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_object_transferred_yes:v2:")
)
async def yes(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        case_id = _require_current_case_callback(
            callback.data,
            prefix="calc_object_transferred_yes",
            state_data=data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return
    if not _base_data_ready(data):
        await _recover_stale_step(callback, state)
        return
    await state.update_data(object_transferred=True)
    await state.set_state(CalculatorStates.waiting_actual_transfer_date)
    await callback.message.edit_text(
        _actual_prompt(),
        reply_markup=_actual_keyboard(case_id),
    )


@router.message(CalculatorStates.waiting_actual_transfer_date)
async def actual(message: Message, state: FSMContext, db):
    data = await state.get_data()
    case_id = _current_case_id(data)
    if case_id <= 0 or not _base_data_ready(data):
        paused = await mark_calculator_draft_paused(state)
        if not paused:
            await state.set_state(None)
        await message.answer(
            "Этот шаг расчёта больше не актуален. Данные не изменены.",
            reply_markup=_result_recovery_keyboard(),
        )
        return
    try:
        actual_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except Exception:
        await message.answer(
            "⚠️ Дата нужна в формате ДД.ММ.ГГГГ. Ранее введённые данные сохранены.",
            reply_markup=_actual_keyboard(case_id),
        )
        return
    if actual_date > date.today():
        await message.answer(
            "⚠️ Фактическая дата передачи не может быть в будущем. Ранее введённые данные сохранены.",
            reply_markup=_actual_keyboard(case_id),
        )
        return
    await state.update_data(actual_transfer_date=actual_date.isoformat())
    await state.set_state(CalculatorStates.waiting_client_type)
    await message.answer(
        _client_type_prompt(),
        reply_markup=_client_type_keyboard(case_id),
    )


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_object_transferred_no:v2:")
)
async def no(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    try:
        _require_current_case_callback(
            callback.data,
            prefix="calc_object_transferred_no",
            state_data=data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return
    if not _base_data_ready(data):
        await _recover_stale_step(callback, state)
        return
    await state.update_data(object_transferred=False, actual_transfer_date=None)
    await state.set_state(CalculatorStates.waiting_client_type)
    await callback.message.edit_text(
        _client_type_prompt(),
        reply_markup=_client_type_keyboard(_current_case_id(await state.get_data())),
    )


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_client_consumer:v2:")
    or str(c.data or "").startswith("calc_client_other:v2:")
)
async def choose_client_type(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    action = (
        "calc_client_consumer"
        if str(callback.data or "").startswith("calc_client_consumer:v2:")
        else "calc_client_other"
    )
    try:
        case_id = _require_current_case_callback(
            callback.data,
            prefix=action,
            state_data=data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return
    client_type = "consumer" if action == "calc_client_consumer" else "other"
    await state.update_data(client_type=client_type)
    await state.set_state(CalculatorStates.waiting_unique_object)
    await callback.message.edit_text(
        _unique_object_prompt(),
        reply_markup=_unique_object_keyboard(case_id),
    )


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_unique_yes:v2:")
    or str(c.data or "").startswith("calc_unique_no:v2:")
)
async def choose_unique_object(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    action = (
        "calc_unique_yes"
        if str(callback.data or "").startswith("calc_unique_yes:v2:")
        else "calc_unique_no"
    )
    try:
        _require_current_case_callback(
            callback.data,
            prefix=action,
            state_data=data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return
    await state.update_data(unique_object=(action == "calc_unique_yes"))
    await calculate_show_callback(callback, state, db)


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_client_unknown:v2:")
    or str(c.data or "").startswith("calc_unique_unknown:v2:")
)
async def legal_fact_unknown(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    if str(callback.data or "").startswith("calc_client_unknown:v2:"):
        action = "calc_client_unknown"
        reason = "Не подтверждён тип участника ДДУ"
    else:
        action = "calc_unique_unknown"
        reason = "Не подтверждён статус уникального объекта"
    try:
        case_id = _require_current_case_callback(
            callback.data,
            prefix=action,
            state_data=data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case = await _bound_case(ctx, user, state)
        await ctx.case_service.transfer_to_m2(
            case=case,
            actor_type="client",
            actor_id=user.id,
            reason=reason,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Legal fact uncertainty could not be routed to lawyer")
        await callback.message.edit_text(
            "Не удалось сохранить переход к ручной проверке. Ваши ответы сохранены.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await finish_calculator_case(state, case_id=case_id)
    await _present_committed_callback(
        callback,
        "⚖️ Для этого расчёта нужен подтверждённый юридический факт. "
        "Система не будет угадывать его или подставлять значение по умолчанию. "
        "Обращение передано в консультационный маршрут.",
        reply_markup=one(
            ("Описать ситуацию", "consult_description_start"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
        saved_notice="Ручная проверка уже зафиксирована.",
    )


async def calc_result(state, db, case):
    data = await state.get_data()
    if (
        not _base_data_ready(data)
        or "object_transferred" not in data
        or str(data.get("client_type") or "") not in {"consumer", "other"}
        or "unique_object" not in data
    ):
        raise ValueError("Данные расчёта неполны. Продолжите с сохранённого шага.")
    return await CalculatorService(db).calculate_and_save(
        case=case,
        contract_price=Decimal(data["contract_price"]),
        planned_transfer_date=date.fromisoformat(data["planned_transfer_date"]),
        calculation_date=date.today(),
        object_transferred=bool(data["object_transferred"]),
        actual_transfer_date=(
            date.fromisoformat(data["actual_transfer_date"])
            if data.get("actual_transfer_date")
            else None
        ),
        client_type=str(data["client_type"]),
        unique_object=bool(data["unique_object"]),
        manual_review_flags=tuple(data.get("manual_review_flags") or ()),
    )


def _result_allows_m1(result) -> bool:
    return int(result.delay_days or 0) > 0 and Decimal(result.penalty_amount or 0) > 0


async def calculate_show_message(message: Message, state: FSMContext, db):
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_message(message)
        case = await _bound_case(ctx, user, state)
        case_id = int(case.id)
        result = await calc_result(state, db, case)
        await db.commit()
    except CalculationManualReviewRequired as error:
        await db.rollback()
        data = await state.get_data()
        case_id = _current_case_id(data)
        await message.answer(
            "⚖️ Автоматический расчёт остановлен: требуется ручная юридическая проверка.\n\n"
            + "\n".join(f"• {reason}" for reason in error.reasons)
            + "\n\nВведённые данные сохранены; система не подставляет спорное значение.",
            reply_markup=one(
                ("💬 Связаться с юристом", "contact_lawyer"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except CalculationRuleError:
        await db.rollback()
        logger.warning(
            "Calculator result blocked: no valid production rule revision for current calculation date"
        )
        data = await state.get_data()
        case_id = _current_case_id(data)
        await message.answer(
            "⚠️ Автоматический расчёт сейчас временно недоступен. "
            "Ваши ответы, включая последнюю дату, сохранены — повторно вводить её не нужно. "
            "Система не подставляет юридические ставки автоматически без опубликованной production-редакции правил. "
            "Сохраните обращение и вернитесь к расчёту после обновления правил.",
            reply_markup=(
                one(
                    ("⬅️ Назад к статусу передачи", bound_case_callback("calc_back_transfer_status", case_id)),
                    ("💾 Сохранить и выйти", "nav_home"),
                    ("📁 Моё дело", "my_case_open"),
                )
                if case_id > 0
                else _result_recovery_keyboard()
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Calculator result could not be saved from message flow")
        data = await state.get_data()
        case_id = _current_case_id(data)
        await message.answer(
            "⚠️ Расчёт не удалось завершить из-за технической ошибки. "
            "Введённые данные сохранены. Повторять ту же дату не нужно. "
            "Сохраните обращение и попробуйте позже.",
            reply_markup=(
                _actual_keyboard(case_id)
                if case_id > 0
                else _result_recovery_keyboard()
            ),
        )
        return

    await finish_calculator_case(state, case_id=case_id)
    try:
        await message.answer(
            format_calculation_result(result),
            reply_markup=result_kb(case_id, allow_m1=_result_allows_m1(result)),
        )
    except Exception:
        logger.exception("Committed calculator result could not be rendered to message")


async def calculate_show_callback(callback: CallbackQuery, state: FSMContext, db):
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        case = await _bound_case(ctx, user, state)
        case_id = int(case.id)
        result = await calc_result(state, db, case)
        await db.commit()
    except CalculationManualReviewRequired as error:
        await db.rollback()
        data = await state.get_data()
        case_id = _current_case_id(data)
        if case_id <= 0:
            await _recover_stale_step(callback, state)
            return
        try:
            ctx = BotContextService(db)
            user = await ctx.get_user_from_callback(callback)
            case = await ctx.case_service.select_case_for_user(
                user_id=int(user.id),
                case_id=case_id,
            )
            await ctx.case_service.transfer_to_m2(
                case=case,
                actor_type="client",
                actor_id=user.id,
                reason="Стоп-фактор автоматического расчёта: " + "; ".join(error.reasons),
            )
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Manual-review calculator stop could not be persisted")
            await callback.message.edit_text(
                "⚖️ Расчёт требует ручной юридической проверки, но переход к юристу "
                "сейчас не удалось сохранить. Ваши ответы не удалены.",
                reply_markup=one(
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return
        await finish_calculator_case(state, case_id=case_id)
        await _present_committed_callback(
            callback,
            "⚖️ Автоматический расчёт остановлен: требуется ручная юридическая проверка.\n\n"
            + "\n".join(f"• {reason}" for reason in error.reasons)
            + "\n\nОбращение передано юристу. Система не подставляла спорное значение.",
            reply_markup=one(
                ("Описать ситуацию", "consult_description_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
            saved_notice="Ручная проверка уже зафиксирована.",
        )
        return
    except CalculationRuleError:
        await db.rollback()
        logger.warning(
            "Calculator result blocked: no valid production rule revision for current calculation date"
        )
        data = await state.get_data()
        case_id = _current_case_id(data)
        if case_id <= 0:
            await _recover_stale_step(callback, state)
            return
        await callback.message.edit_text(
            "⚠️ Автоматический расчёт сейчас временно недоступен. "
            "Ваши ответы сохранены. Система не подставляет юридические ставки "
            "автоматически без утверждённых правил. Сохраните обращение и "
            "вернитесь к расчёту после обновления правил.",
            reply_markup=one(
                (
                    "⬅️ Изменить дату по ДДУ",
                    bound_case_callback("calc_back_planned", case_id),
                ),
                ("💾 Сохранить и выйти", "nav_home"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Calculator result could not be saved from callback flow")
        data = await state.get_data()
        case_id = _current_case_id(data)
        if case_id <= 0:
            await _recover_stale_step(callback, state)
            return
        await callback.message.edit_text(
            "⚠️ Расчёт не удалось завершить из-за технической ошибки. "
            "Введённые данные сохранены. Повторять действие не нужно — "
            "сохраните обращение и попробуйте позже.",
            reply_markup=one(
                (
                    "⬅️ Изменить дату по ДДУ",
                    bound_case_callback("calc_back_planned", case_id),
                ),
                ("💾 Сохранить и выйти", "nav_home"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    await finish_calculator_case(state, case_id=case_id)
    await _present_committed_callback(
        callback,
        format_calculation_result(result),
        reply_markup=result_kb(case_id, allow_m1=_result_allows_m1(result)),
        saved_notice="Расчёт уже сохранён.",
    )


def result_kb(case_id: int, *, allow_m1: bool = True):
    items: list[tuple[str, str]] = []
    if allow_m1:
        items.append(("Продолжить ведение дела", f"calc_continue_m1:v2:{case_id}"))
    items.extend(
        [
            ("🔎 Основания и детализация", f"calc_details:v2:{case_id}"),
            ("💬 Перейти к консультации", f"calc_to_m2:v2:{case_id}"),
            ("Пока изучаю вопрос", f"calc_postpone:v2:{case_id}"),
            ("🧮 Изменить данные и пересчитать", f"calc_repeat:v2:{case_id}"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    return one(*items)


@router.callback_query(lambda c: str(c.data or "").startswith("calc_details:v2:"))
async def calculation_details(callback: CallbackQuery, db):
    try:
        case_id = _parse_case_callback(callback.data, "calc_details")
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        await ctx.case_service.select_case_for_user(
            user_id=int(user.id),
            case_id=case_id,
        )
        calculation = await CalculatorService(db).latest_calculation_for_case(
            case_id=case_id
        )
        if calculation is None:
            raise LookupError("Расчёт не найден")
        await db.commit()
    except Exception:
        await db.rollback()
        await callback.answer(
            "Детализация недоступна или эта кнопка устарела.",
            show_alert=True,
        )
        return

    await callback.message.answer(
        format_calculation_details(calculation),
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
    await callback.answer("Открыта сохранённая детализация расчёта.")


@router.callback_query(
    lambda c: str(c.data or "").startswith("calc_unknown_price:v2:")
    or str(c.data or "").startswith("calc_unknown_date:v2:")
    or str(c.data or "").startswith("calc_future_date_consult:v2:")
)
async def unknown_calc_data(callback: CallbackQuery, state: FSMContext, db):
    raw_action = str(callback.data or "")
    if raw_action.startswith("calc_unknown_price:v2:"):
        action = "calc_unknown_price"
        reason = "Клиент не знает стоимость"
        success_text = (
            "Без этих данных расчёт будет неточным. Обращение переведено в консультационный маршрут.\n\n"
            "Опишите ситуацию — юрист поможет разобраться по документам и срокам."
        )
    elif raw_action.startswith("calc_unknown_date:v2:"):
        action = "calc_unknown_date"
        reason = "Клиент не знает дату передачи"
        success_text = (
            "Без этих данных расчёт будет неточным. Обращение переведено в консультационный маршрут.\n\n"
            "Опишите ситуацию — юрист поможет разобраться по документам и срокам."
        )
    else:
        action = "calc_future_date_consult"
        reason = "Срок передачи по ДДУ ещё не наступил; клиент запросил консультацию"
        success_text = (
            "Срок передачи по ДДУ ещё не наступил. Обращение переведено в консультационный маршрут по вашему выбору.\n\n"
            "Опишите ситуацию — юрист сможет проверить договор, срок и возможные действия до его наступления."
        )

    data = await state.get_data()
    try:
        case_id = _require_current_case_callback(
            callback.data,
            prefix=action,
            state_data=data,
        )
    except Exception:
        await _recover_stale_step(callback, state)
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case = await _bound_case(ctx, user, state)
        await ctx.case_service.transfer_to_m2(
            case=case,
            actor_type="client",
            actor_id=user.id,
            reason=reason,
        )
        await ctx.case_service.select_case_for_user(
            user_id=int(user.id),
            case_id=case_id,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Calculator fallback to consultation could not be saved")
        await callback.message.edit_text(
            "Переход к консультации временно не сохранён. Повторите действие.",
            reply_markup=one(
                (
                    "🔄 Повторить переход",
                    bound_case_callback(action, case_id),
                ),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await finish_calculator_case(state, case_id=case_id)
    await _present_committed_callback(
        callback,
        success_text,
        reply_markup=one(
            ("Описать ситуацию", "consult_description_start"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
        saved_notice="Переход к консультации уже сохранён.",
    )


@router.callback_query(lambda c: str(c.data or "").startswith("calc_continue_m1:v2:"))
async def to_m1(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case_id = _parse_case_callback(callback.data, "calc_continue_m1")
        case = await ctx.case_service.select_case_for_user(
            user_id=int(user.id),
            case_id=case_id,
        )
        await CalculatorService(db).require_m1_eligible_calculation(case_id=case_id)
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.CLIENT_DECISION,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент выбрал продолжение работы по М1",
        )
        await db.commit()
    except CalculatorRouteEligibilityError:
        await db.rollback()
        await callback.message.edit_text(
            "По последнему сохранённому расчёту просрочка или положительная сумма неустойки отсутствует. "
            "Стандартный маршрут взыскания из этого результата не открыт.\n\n"
            "Можно изменить данные расчёта или перейти к консультации.",
            reply_markup=one(
                (
                    "💬 Перейти к консультации",
                    bound_case_callback("calc_to_m2", case_id),
                ),
                (
                    "🧮 Изменить данные и пересчитать",
                    bound_case_callback("calc_repeat", case_id),
                ),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Calculator M1 continuation could not be saved")
        await callback.message.edit_text(
            "Продолжение дела временно не сохранено или эта кнопка устарела.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await _present_committed_callback(
        callback,
        "📄 Следующий шаг — согласие на обработку персональных данных. После подтверждения можно будет передать документы юристу.",
        reply_markup=one(
            ("Перейти к согласию", "consent_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
        saved_notice="Этап дела уже сохранён.",
    )


@router.callback_query(lambda c: str(c.data or "").startswith("calc_to_m2:v2:"))
async def to_m2(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case_id = _parse_case_callback(callback.data, "calc_to_m2")
        case = await ctx.case_service.select_case_for_user(
            user_id=int(user.id),
            case_id=case_id,
        )
        await ctx.case_service.transfer_to_m2(
            case=case,
            actor_type="client",
            actor_id=user.id,
            reason="Клиент выбрал консультацию",
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Calculator consultation transition could not be saved")
        await callback.message.edit_text(
            "Переход к консультации временно не сохранён или эта кнопка устарела.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await _present_committed_callback(
        callback,
        "💬 Обращение переведено в консультационный маршрут. Опишите ситуацию своими словами — юрист увидит описание перед встречей.",
        reply_markup=one(
            ("Описать ситуацию", "consult_description_start"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
        saved_notice="Переход к консультации уже сохранён.",
    )


@router.callback_query(lambda c: str(c.data or "").startswith("calc_postpone:v2:"))
async def postpone(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case_id = _parse_case_callback(callback.data, "calc_postpone")
        await ctx.case_service.select_case_for_user(
            user_id=int(user.id),
            case_id=case_id,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        await callback.message.edit_text(
            "Эта кнопка больше не актуальна. Откройте нужное дело из кабинета.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await callback.message.edit_text(
        "📌 Расчёт сохранён. Вернуться к нему и следующему шагу можно через «Моё дело».",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: c.data in {"calc_continue_m1", "calc_to_m2", "calc_postpone"}
)
async def legacy_unbound_result_action(callback: CallbackQuery):
    """Never mutate a Case from pre-v2 result buttons without case provenance."""
    await callback.message.edit_text(
        "Эта кнопка относится к старой версии экрана и больше не выполняет действие без номера обращения.\n\n"
        "Откройте «Моё дело» — там будет показан актуальный шаг без риска изменить другое обращение.",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🧮 Новый расчёт", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: str(c.data or "") in _LEGACY_UNBOUND_CALCULATOR_ACTIONS)
async def legacy_unbound_calculator_flow_action(callback: CallbackQuery):
    """Old questionnaire buttons cannot act on whichever Case is current now."""

    await callback.message.edit_text(
        "Эта кнопка расчёта относится к старой версии экрана и не содержит номер обращения. "
        "Действие не выполнено. Откройте нужное дело и продолжите его актуальный расчёт.",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🧮 Новый расчёт", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )