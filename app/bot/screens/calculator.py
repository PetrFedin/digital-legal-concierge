import logging
from datetime import date, datetime
from decimal import Decimal

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.calculator_draft import (
    clear_calculator_draft_metadata,
    draft_step,
    draft_step_label,
    has_saved_calculator_draft,
)
from app.bot.client_case_view import load_client_case_view
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import CalculatorStates
from app.domain.calculator.calculator_result_formatter import format_calculation_result
from app.domain.calculator.calculator_service import CalculatorService
from app.domain.calculator.penalty_calculator import parse_money
from app.domain.statuses.case_statuses import CaseStatus

logger = logging.getLogger(__name__)
router = Router()


def _price_prompt(current: str | None = None) -> str:
    current_note = f"\nСейчас сохранено: {current} ₽. Введите новую сумму." if current else ""
    return (
        "🧮 Расчёт неустойки · шаг 1 из 4\n\n"
        "Ответьте на несколько вопросов. Расчёт будет предварительным и не является юридическим заключением.\n\n"
        "💰 Введите стоимость объекта по ДДУ в рублях.\n"
        f"Например: 8500000{current_note}"
    )


def _planned_prompt(current: str | None = None) -> str:
    current_note = f"\nСейчас сохранена дата: {current}." if current else ""
    return (
        "🧮 Расчёт неустойки · шаг 2 из 4\n\n"
        "📅 Укажите дату передачи объекта по ДДУ. Формат ДД.ММ.ГГГГ."
        f"{current_note}"
    )


def _transfer_prompt() -> str:
    return "🧮 Расчёт неустойки · шаг 3 из 4\n\n🏗 Объект уже передан по акту?"


def _actual_prompt() -> str:
    return (
        "🧮 Расчёт неустойки · шаг 4 из 4\n\n"
        "📅 Укажите дату фактической передачи по акту. Формат ДД.ММ.ГГГГ"
    )


def _planned_keyboard():
    return one(
        ("Не знаю дату", "calc_unknown_date"),
        ("⬅️ Изменить стоимость", "calc_back_price"),
        ("💾 Сохранить и выйти", "nav_home"),
    )


def _transfer_keyboard():
    return one(
        ("Да, передан", "calc_object_transferred_yes"),
        ("Нет, не передан", "calc_object_transferred_no"),
        ("⬅️ Изменить дату по ДДУ", "calc_back_planned"),
        ("💾 Сохранить и выйти", "nav_home"),
    )


def _actual_keyboard():
    return one(
        ("⬅️ Назад к статусу передачи", "calc_back_transfer_status"),
        ("💾 Сохранить и выйти", "nav_home"),
    )


def _result_recovery_keyboard():
    return one(
        ("🧮 Начать расчёт заново", "calc_start"),
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
    return "\n".join(rows) or "Введённые значения сохранены."


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
    await state.clear()
    await callback.message.edit_text(
        "Этот шаг расчёта больше не актуален. Данные не изменены.\n\n"
        "Откройте текущее дело или начните расчёт заново, если активного дела уже нет.",
        reply_markup=_result_recovery_keyboard(),
    )


def _base_data_ready(data: dict) -> bool:
    return bool(data.get("contract_price") and data.get("planned_transfer_date"))


async def _start_fresh(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CalculatorStates.waiting_contract_price)
    await callback.message.edit_text(
        _price_prompt(),
        reply_markup=one(
            ("Не знаю стоимость", "calc_unknown_price"),
            ("💾 Сохранить и выйти", "nav_home"),
        ),
    )


async def _resume_draft(callback: CallbackQuery, state: FSMContext) -> None:
    data = await clear_calculator_draft_metadata(state)
    step = draft_step(data)
    if step == "price":
        await state.set_state(CalculatorStates.waiting_contract_price)
        await callback.message.edit_text(
            _price_prompt(str(data.get("contract_price") or "") or None),
            reply_markup=one(
                ("Не знаю стоимость", "calc_unknown_price"),
                ("💾 Сохранить и выйти", "nav_home"),
            ),
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
        await callback.message.edit_text(_planned_prompt(current), reply_markup=_planned_keyboard())
        return
    if step == "transfer_status":
        if not _base_data_ready(data):
            await _start_fresh(callback, state)
            return
        await state.set_state(CalculatorStates.waiting_object_transfer_status)
        await callback.message.edit_text(_transfer_prompt(), reply_markup=_transfer_keyboard())
        return
    if not _base_data_ready(data):
        await _start_fresh(callback, state)
        return
    await state.set_state(CalculatorStates.waiting_actual_transfer_date)
    await callback.message.edit_text(_actual_prompt(), reply_markup=_actual_keyboard())


@router.callback_query(lambda c: c.data == "calc_start")
async def calc_start(callback: CallbackQuery, state: FSMContext, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        view = await load_client_case_view(db, case)
        primary_action = (
            (
                f"▶️ {view.action.label}",
                f"next_action:v2:{view.case_id}:{view.action_key}",
            )
            if view.action
            else ("📁 Открыть текущее дело", "my_case_open")
        )
        await state.clear()
        await callback.message.edit_text(
            "📁 У вас уже есть активное дело.\n\n"
            "Чтобы не смешивать расчёты, документы и статусы разных обращений, "
            "сначала продолжите текущее дело. Новый расчёт станет доступен после "
            "его завершения.",
            reply_markup=one(
                primary_action,
                ("📁 Моё дело", "my_case_open"),
                ("💬 Связаться с юристом", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    data = await state.get_data()
    if has_saved_calculator_draft(data):
        await state.set_state(None)
        await callback.message.edit_text(
            "📝 Сохранён незавершённый расчёт\n\n"
            f"{_draft_summary(data)}\n\n"
            f"Следующий шаг: {draft_step_label(data)}.\n"
            "Продолжите с сохранённого места или начните заново — сброс потребует подтверждения.",
            reply_markup=one(
                ("▶️ Продолжить расчёт", "calc_resume"),
                ("Начать заново", "calc_restart_confirm"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _start_fresh(callback, state)


@router.callback_query(lambda c: c.data == "calc_resume")
async def resume_saved_calculation(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not has_saved_calculator_draft(data):
        await _start_fresh(callback, state)
        return
    await _resume_draft(callback, state)


@router.callback_query(lambda c: c.data == "calc_restart_confirm")
async def confirm_restart_calculation(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not has_saved_calculator_draft(data):
        await _start_fresh(callback, state)
        return
    await state.set_state(None)
    await callback.message.edit_text(
        "Начать расчёт заново?\n\n"
        "Сохранённые ответы текущего незавершённого расчёта будут удалены. "
        "Созданное дело и уже завершённые расчёты это действие не меняет.",
        reply_markup=one(
            ("Да, удалить черновик", "calc_restart"),
            ("↩️ Вернуться к черновику", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "calc_restart")
async def restart_calculation(callback: CallbackQuery, state: FSMContext):
    await _start_fresh(callback, state)


@router.callback_query(lambda c: c.data == "calc_back_price")
async def back_price(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.set_state(CalculatorStates.waiting_contract_price)
    await callback.message.edit_text(
        _price_prompt(str(data.get("contract_price") or "") or None),
        reply_markup=one(
            ("Не знаю стоимость", "calc_unknown_price"),
            ("💾 Сохранить и выйти", "nav_home"),
        ),
    )


@router.message(CalculatorStates.waiting_contract_price)
async def price(message: Message, state: FSMContext):
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
    await message.answer(_planned_prompt(), reply_markup=_planned_keyboard())


@router.callback_query(lambda c: c.data == "calc_back_planned")
async def back_planned(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
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
        reply_markup=_planned_keyboard(),
    )


@router.message(CalculatorStates.waiting_planned_transfer_date)
async def planned(message: Message, state: FSMContext):
    try:
        planned_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except Exception:
        await message.answer(
            "⚠️ Дата нужна в формате ДД.ММ.ГГГГ. Ранее введённая стоимость сохранена.",
            reply_markup=_planned_keyboard(),
        )
        return
    if planned_date > date.today():
        await message.answer(
            "Дата передачи ещё не наступила. Автоматический расчёт сейчас невозможен. "
            "Можно обсудить будущий срок с юристом или исправить дату.",
            reply_markup=one(
                ("💬 Перейти к юристу", "calc_unknown_date"),
                ("⬅️ Изменить стоимость", "calc_back_price"),
                ("💾 Сохранить и выйти", "nav_home"),
            ),
        )
        return
    await state.update_data(planned_transfer_date=planned_date.isoformat())
    await state.set_state(CalculatorStates.waiting_object_transfer_status)
    await message.answer(_transfer_prompt(), reply_markup=_transfer_keyboard())


@router.callback_query(lambda c: c.data == "calc_back_transfer_status")
async def back_transfer_status(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not _base_data_ready(data):
        await _recover_stale_step(callback, state)
        return
    await state.set_state(CalculatorStates.waiting_object_transfer_status)
    await callback.message.edit_text(_transfer_prompt(), reply_markup=_transfer_keyboard())


@router.callback_query(lambda c: c.data == "calc_object_transferred_yes")
async def yes(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not _base_data_ready(data):
        await _recover_stale_step(callback, state)
        return
    await state.update_data(object_transferred=True)
    await state.set_state(CalculatorStates.waiting_actual_transfer_date)
    await callback.message.edit_text(_actual_prompt(), reply_markup=_actual_keyboard())


@router.message(CalculatorStates.waiting_actual_transfer_date)
async def actual(message: Message, state: FSMContext, db):
    try:
        actual_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except Exception:
        await message.answer(
            "⚠️ Дата нужна в формате ДД.ММ.ГГГГ. Ранее введённые данные сохранены.",
            reply_markup=_actual_keyboard(),
        )
        return
    data = await state.get_data()
    if not _base_data_ready(data):
        await state.clear()
        await message.answer(
            "Этот шаг расчёта больше не актуален. Данные не изменены.",
            reply_markup=_result_recovery_keyboard(),
        )
        return
    planned_date = date.fromisoformat(data["planned_transfer_date"])
    if actual_date < planned_date:
        await message.answer(
            "⚠️ Фактическая дата передачи не может быть раньше даты по ДДУ. Ранее введённые данные сохранены.",
            reply_markup=_actual_keyboard(),
        )
        return
    if actual_date > date.today():
        await message.answer(
            "⚠️ Фактическая дата передачи не может быть в будущем. Ранее введённые данные сохранены.",
            reply_markup=_actual_keyboard(),
        )
        return
    await state.update_data(actual_transfer_date=actual_date.isoformat())
    await calculate_show_message(message, state, db)


@router.callback_query(lambda c: c.data == "calc_object_transferred_no")
async def no(callback: CallbackQuery, state: FSMContext, db):
    data = await state.get_data()
    if not _base_data_ready(data):
        await _recover_stale_step(callback, state)
        return
    await state.update_data(object_transferred=False, actual_transfer_date=None)
    await calculate_show_callback(callback, state, db)


async def calc_result(state, db, case):
    data = await state.get_data()
    if not _base_data_ready(data) or "object_transferred" not in data:
        raise ValueError("Данные расчёта устарели. Начните расчёт заново.")
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
    )


async def calculate_show_message(message: Message, state: FSMContext, db):
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_message(message)
        case = await ctx.get_or_create_active_case_for_user(user)
        result = await calc_result(state, db, case)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Calculator result could not be saved from message flow")
        await message.answer(
            "⚠️ Расчёт временно не сохранён. Введённые данные остаются в текущем шаге. "
            "Повторите дату или вернитесь назад.",
            reply_markup=_actual_keyboard(),
        )
        return

    await state.clear()
    try:
        await message.answer(format_calculation_result(result), reply_markup=result_kb())
    except Exception:
        logger.exception("Committed calculator result could not be rendered to message")


async def calculate_show_callback(callback: CallbackQuery, state: FSMContext, db):
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        case = await ctx.get_or_create_active_case_for_user(user)
        result = await calc_result(state, db, case)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Calculator result could not be saved from callback flow")
        await callback.message.edit_text(
            "⚠️ Расчёт временно не сохранён. Введённые данные сохранены. Повторите действие.",
            reply_markup=one(
                ("🔄 Повторить расчёт", "calc_object_transferred_no"),
                ("⬅️ Изменить дату по ДДУ", "calc_back_planned"),
                ("💾 Сохранить и выйти", "nav_home"),
            ),
        )
        return

    await state.clear()
    await _present_committed_callback(
        callback,
        format_calculation_result(result),
        reply_markup=result_kb(),
        saved_notice="Расчёт уже сохранён.",
    )


def result_kb():
    return one(
        ("Продолжить ведение дела", "calc_continue_m1"),
        ("💬 Перейти к консультации", "calc_to_m2"),
        ("Пока изучаю вопрос", "calc_postpone"),
        ("🏠 Главная", "nav_home"),
    )


@router.callback_query(lambda c: c.data in {"calc_unknown_price", "calc_unknown_date"})
async def unknown_calc_data(callback: CallbackQuery, state: FSMContext, db):
    await state.clear()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.get_or_create_active_case_for_user(user)
    reason = (
        "Клиент не знает стоимость"
        if callback.data == "calc_unknown_price"
        else "Клиент не знает дату передачи"
    )
    try:
        await ctx.case_service.transfer_to_m2(
            case=case,
            actor_type="client",
            actor_id=user.id,
            reason=reason,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Calculator fallback to consultation could not be saved")
        await callback.message.edit_text(
            "Переход к консультации временно не сохранён. Повторите действие.",
            reply_markup=one(
                ("🔄 Повторить переход", callback.data),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await _present_committed_callback(
        callback,
        "Без этих данных расчёт будет неточным. Обращение переведено в консультационный маршрут.\n\n"
        "Опишите ситуацию — юрист поможет разобраться по документам и срокам.",
        reply_markup=one(
            ("Описать ситуацию", "consult_description_start"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
        saved_notice="Переход к консультации уже сохранён.",
    )


@router.callback_query(lambda c: c.data == "calc_continue_m1")
async def to_m1(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.get_or_create_active_case_for_user(user)
    try:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.CLIENT_DECISION,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент выбрал продолжение работы по М1",
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Calculator M1 continuation could not be saved")
        await callback.message.edit_text(
            "Продолжение дела временно не сохранено. Повторите действие.",
            reply_markup=one(
                ("🔄 Повторить", "calc_continue_m1"),
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


@router.callback_query(lambda c: c.data == "calc_to_m2")
async def to_m2(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.get_or_create_active_case_for_user(user)
    try:
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
            "Переход к консультации временно не сохранён. Повторите действие.",
            reply_markup=one(
                ("🔄 Повторить", "calc_to_m2"),
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


@router.callback_query(lambda c: c.data == "calc_postpone")
async def postpone(callback: CallbackQuery):
    await callback.message.edit_text(
        "📌 Расчёт сохранён. Вернуться к нему и следующему шагу можно через «Моё дело».",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )