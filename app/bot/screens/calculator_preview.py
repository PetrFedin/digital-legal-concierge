from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import PreviewCalculatorStates
from app.config import settings
from app.domain.calculator.calculator_result_formatter import format_calculation_result
from app.domain.calculator.calculator_service import CalculatorService
from app.domain.calculator.intake_service import CalculationIntakeService, INTAKE_COMPLETED
from app.domain.calculator.penalty_calculator import parse_money
from app.domain.calculator.rule_engine import (
    CalculationManualReviewRequired,
    CalculationRuleEngine,
    CalculationRuleError,
    RuleBasedCalculationInput,
)
from app.domain.calculator.rule_revision_service import CalculationRuleRevisionService
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case
from app.models.case_creation_request import CaseCreationRequest

router = Router()
logger = logging.getLogger(__name__)

_PREVIEW_FLAG = "calculator_preview_v1"
_PREVIEW_FIELDS = {
    "contract_price",
    "planned_transfer_date",
    "object_transferred",
    "actual_transfer_date",
    "client_type",
    "unique_object",
    "manual_review_flags",
    "preview_calculation_date",
    "preview_rule_revision_key",
    "preview_rule_snapshot_sha256",
    "preview_materialization_id",
}


def _header() -> str:
    return (
        "👀 БЫСТРЫЙ РАСЧЁТ · БЕЗ СОХРАНЕНИЯ\n"
        "Ответы пока существуют только в этом временном сеансе. "
        "Дело, история и карточка клиента не создаются, пока вы сами не нажмёте "
        "«Сохранить расчёт и продолжить».\n\n"
    )


def _price_prompt() -> str:
    return _header() + "Шаг 1 из 6\n\n💰 Введите стоимость объекта по ДДУ в рублях.\nНапример: 8500000"


def _planned_prompt(current: str | None = None) -> str:
    note = f"\nСейчас введено: {current}." if current else ""
    return _header() + "Шаг 2 из 6\n\n📅 Укажите дату передачи объекта по ДДУ. Формат ДД.ММ.ГГГГ." + note


def _transfer_prompt() -> str:
    return _header() + "Шаг 3 из 6\n\n🏗 Объект уже передан по акту?"


def _actual_prompt() -> str:
    return _header() + "Шаг 4 из 6\n\n📅 Укажите дату фактической передачи по акту. Формат ДД.ММ.ГГГГ."


def _client_type_prompt() -> str:
    return (
        _header()
        + "Шаг 5 из 6\n\n"
        "👤 Вы являетесь гражданином и заключали ДДУ для личных, семейных, "
        "домашних или иных нужд, не связанных с предпринимательской деятельностью?"
    )


def _unique_prompt() -> str:
    return (
        _header()
        + "Шаг 6 из 6\n\n"
        "🏢 Отнесён ли объект по проектной документации к уникальным объектам?\n\n"
        "Если вы не уверены, бот не будет угадывать этот юридически значимый факт."
    )


def _exit_buttons(*items: tuple[str, str]):
    return one(*items, ("🏠 Выйти без сохранения", "nav_home"))


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Экран уже актуален.")


def _clean_preview_data(data: dict) -> dict:
    return {key: value for key, value in data.items() if key in _PREVIEW_FIELDS}


def _valid_preview_id(value: object) -> str | None:
    clean = str(value or "").strip().lower()
    return clean if re.fullmatch(r"[0-9a-f]{32}", clean) else None


def _preview_operation_key(preview_id: str) -> str:
    clean = _valid_preview_id(preview_id)
    if clean is None:
        raise ValueError("Некорректный идентификатор предварительного расчёта")
    return f"calculator_preview_save:{clean}"


def _preview_id_from_callback(data: str | None) -> str | None:
    value = str(data or "")
    prefix = "preview_calc_save:v2:"
    if value == "preview_calc_save":
        return None
    if not value.startswith(prefix):
        return None
    return _valid_preview_id(value[len(prefix) :])


def _ready(data: dict) -> bool:
    return bool(
        data.get("contract_price")
        and data.get("planned_transfer_date")
        and isinstance(data.get("object_transferred"), bool)
        and (
            data.get("object_transferred") is False
            or bool(data.get("actual_transfer_date"))
        )
        and str(data.get("client_type") or "") in {"consumer", "other"}
        and isinstance(data.get("unique_object"), bool)
    )


async def _calculate_preview(db, data: dict):
    if not _ready(data):
        raise ValueError("Предварительный расчёт ещё не заполнен полностью")

    calculation_date = date.today()
    revision = await CalculationRuleRevisionService(db).resolve(
        calculation_date=calculation_date
    )
    result = CalculationRuleEngine().calculate(
        RuleBasedCalculationInput(
            contract_price=Decimal(str(data["contract_price"])),
            planned_transfer_date=date.fromisoformat(str(data["planned_transfer_date"])),
            calculation_date=calculation_date,
            object_transferred=bool(data["object_transferred"]),
            actual_transfer_date=(
                date.fromisoformat(str(data["actual_transfer_date"]))
                if data.get("actual_transfer_date")
                else None
            ),
            client_type=str(data["client_type"]),
            unique_object=bool(data["unique_object"]),
            manual_review_flags=tuple(data.get("manual_review_flags") or ()),
        ),
        rule_revision_id=int(revision.id),
        rule_revision_key=str(revision.revision_key),
        rule_snapshot_sha256=str(revision.rules_sha256),
        rule_snapshot=dict(revision.rules),
    )
    return result


async def _show_result(callback: CallbackQuery, state: FSMContext, db) -> None:
    data = await state.get_data()
    try:
        result = await _calculate_preview(db, data)
        await db.rollback()
    except CalculationManualReviewRequired as error:
        await db.rollback()
        await state.update_data(
            manual_review_flags=list(error.reasons),
        )
        await _safe_edit(
            callback,
            _header()
            + "⚖️ Автоматический расчёт остановлен: нужна проверка юристом.\n\n"
            + "\n".join(f"• {reason}" for reason in error.reasons)
            + "\n\nНичего из этого предварительного сеанса не записано в дело.",
            reply_markup=_exit_buttons(
                ("💬 Обратиться к юристу", "contact_lawyer"),
                ("🔄 Начать расчёт заново", "preview_calc_start"),
            ),
        )
        return
    except CalculationRuleError:
        await db.rollback()
        await _safe_edit(
            callback,
            _header()
            + "⚠️ Автоматический расчёт временно недоступен: для сегодняшней даты "
            "нет однозначной опубликованной редакции юридических правил. "
            "Бот не подставляет ставку или норму по догадке.",
            reply_markup=_exit_buttons(
                ("🔄 Проверить ещё раз", "preview_calc_recalculate"),
                ("💬 Обратиться к юристу", "contact_lawyer"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Preview calculator failed")
        await _safe_edit(
            callback,
            _header()
            + "⚠️ Не удалось выполнить предварительный расчёт. "
            "Постоянное дело не создавалось.",
            reply_markup=_exit_buttons(
                ("🔄 Начать заново", "preview_calc_start"),
            ),
        )
        return

    preview_id = _valid_preview_id(data.get("preview_materialization_id"))
    if preview_id is None:
        preview_id = uuid4().hex
    await state.update_data(
        preview_calculation_date=result.calculation_date.isoformat(),
        preview_rule_revision_key=result.rule_revision_key,
        preview_rule_snapshot_sha256=result.rule_snapshot_sha256,
        preview_materialization_id=preview_id,
        manual_review_flags=[],
    )
    await state.set_state(PreviewCalculatorStates.result_ready)

    await _safe_edit(
        callback,
        _header()
        + format_calculation_result(result)
        + "\n\n"
        "Если это просто ознакомительный расчёт — можно выйти, и он не появится в «Моём деле». "
        "Для продолжения с документами/услугой сначала сохраните его явно.",
        reply_markup=_exit_buttons(
            (
                "💾 Сохранить расчёт и продолжить",
                f"preview_calc_save:v2:{preview_id}",
            ),
            ("🔄 Изменить данные", "preview_calc_start"),
        ),
    )


@router.callback_query(lambda c: c.data == "preview_calc_start")
async def preview_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.update_data(
        preview_mode=_PREVIEW_FLAG,
        preview_materialization_id=uuid4().hex,
    )
    await state.set_state(PreviewCalculatorStates.waiting_contract_price)
    await _safe_edit(
        callback,
        _price_prompt(),
        reply_markup=_exit_buttons(),
    )


@router.message(PreviewCalculatorStates.waiting_contract_price)
async def preview_price(message: Message, state: FSMContext):
    try:
        amount = parse_money(message.text)
    except Exception:
        await message.answer(
            "⚠️ Введите сумму цифрами. Например: 8500000. "
            "Ничего не сохранено в карточке дела.",
            reply_markup=_exit_buttons(),
        )
        return
    await state.update_data(contract_price=str(amount))
    await state.set_state(PreviewCalculatorStates.waiting_planned_transfer_date)
    await message.answer(
        _planned_prompt(),
        reply_markup=_exit_buttons(),
    )


@router.message(PreviewCalculatorStates.waiting_planned_transfer_date)
async def preview_planned_date(message: Message, state: FSMContext):
    try:
        planned = datetime.strptime(str(message.text or "").strip(), "%d.%m.%Y").date()
    except Exception:
        await message.answer(
            "⚠️ Дата нужна в формате ДД.ММ.ГГГГ. Временные ответы остаются только в этом сеансе.",
            reply_markup=_exit_buttons(),
        )
        return

    await state.update_data(
        planned_transfer_date=planned.isoformat(),
        object_transferred=None,
        actual_transfer_date=None,
        client_type=None,
        unique_object=None,
    )
    if planned > date.today():
        await state.set_state(PreviewCalculatorStates.waiting_planned_transfer_date)
        await message.answer(
            _header()
            + "ℹ️ Срок передачи по ДДУ ещё не наступил. "
            "Просрочку на будущую договорную дату сейчас не рассчитываем.\n\n"
            f"Введённая дата: {planned.strftime('%d.%m.%Y')}.",
            reply_markup=_exit_buttons(
                ("✏️ Ввести другую дату", "preview_calc_back_planned"),
                ("💬 Обратиться к юристу", "contact_lawyer"),
            ),
        )
        return

    await state.set_state(PreviewCalculatorStates.waiting_object_transfer_status)
    await message.answer(
        _transfer_prompt(),
        reply_markup=_exit_buttons(
            ("Да, передан", "preview_transfer_yes"),
            ("Нет, не передан", "preview_transfer_no"),
        ),
    )


@router.callback_query(lambda c: c.data == "preview_calc_back_planned")
async def preview_back_planned(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current = None
    if data.get("planned_transfer_date"):
        try:
            current = date.fromisoformat(str(data["planned_transfer_date"])).strftime("%d.%m.%Y")
        except ValueError:
            current = None
    await state.set_state(PreviewCalculatorStates.waiting_planned_transfer_date)
    await _safe_edit(
        callback,
        _planned_prompt(current),
        reply_markup=_exit_buttons(),
    )


@router.callback_query(lambda c: c.data in {"preview_transfer_yes", "preview_transfer_no"})
async def preview_transfer(callback: CallbackQuery, state: FSMContext):
    transferred = callback.data == "preview_transfer_yes"
    await state.update_data(
        object_transferred=transferred,
        actual_transfer_date=None,
        client_type=None,
        unique_object=None,
    )
    if transferred:
        await state.set_state(PreviewCalculatorStates.waiting_actual_transfer_date)
        await _safe_edit(callback, _actual_prompt(), reply_markup=_exit_buttons())
        return
    await state.set_state(PreviewCalculatorStates.waiting_client_type)
    await _safe_edit(
        callback,
        _client_type_prompt(),
        reply_markup=_exit_buttons(
            ("Да, гражданин для личных нужд", "preview_client_consumer"),
            ("Нет, иной участник", "preview_client_other"),
            ("Не уверен", "preview_client_unknown"),
        ),
    )


@router.message(PreviewCalculatorStates.waiting_actual_transfer_date)
async def preview_actual_date(message: Message, state: FSMContext):
    try:
        actual = datetime.strptime(str(message.text or "").strip(), "%d.%m.%Y").date()
    except Exception:
        await message.answer(
            "⚠️ Дата нужна в формате ДД.ММ.ГГГГ.",
            reply_markup=_exit_buttons(),
        )
        return
    if actual > date.today():
        await message.answer(
            "⚠️ Фактическая дата передачи не может быть в будущем.",
            reply_markup=_exit_buttons(),
        )
        return

    await state.update_data(actual_transfer_date=actual.isoformat())
    await state.set_state(PreviewCalculatorStates.waiting_client_type)
    await message.answer(
        _client_type_prompt(),
        reply_markup=_exit_buttons(
            ("Да, гражданин для личных нужд", "preview_client_consumer"),
            ("Нет, иной участник", "preview_client_other"),
            ("Не уверен", "preview_client_unknown"),
        ),
    )


@router.callback_query(
    lambda c: c.data in {
        "preview_client_consumer",
        "preview_client_other",
        "preview_client_unknown",
    }
)
async def preview_client_type(callback: CallbackQuery, state: FSMContext):
    if callback.data == "preview_client_unknown":
        await _safe_edit(
            callback,
            _header()
            + "⚖️ Для автоматического расчёта нужно подтвердить тип участника ДДУ. "
            "В режиме без сохранения бот не создаёт обращение и не угадывает ответ.",
            reply_markup=_exit_buttons(
                ("💬 Обратиться к юристу", "contact_lawyer"),
                ("🔄 Начать заново", "preview_calc_start"),
            ),
        )
        return

    await state.update_data(
        client_type=(
            "consumer" if callback.data == "preview_client_consumer" else "other"
        ),
        unique_object=None,
    )
    await state.set_state(PreviewCalculatorStates.waiting_unique_object)
    await _safe_edit(
        callback,
        _unique_prompt(),
        reply_markup=_exit_buttons(
            ("Да, уникальный объект", "preview_unique_yes"),
            ("Нет", "preview_unique_no"),
            ("Не знаю", "preview_unique_unknown"),
        ),
    )


@router.callback_query(
    lambda c: c.data in {"preview_unique_yes", "preview_unique_no", "preview_unique_unknown"}
)
async def preview_unique(callback: CallbackQuery, state: FSMContext, db):
    if callback.data == "preview_unique_unknown":
        await _safe_edit(
            callback,
            _header()
            + "⚖️ Статус уникального объекта должен быть подтверждён. "
            "Бот не подставляет «нет» по умолчанию. Временный расчёт не записан.",
            reply_markup=_exit_buttons(
                ("💬 Обратиться к юристу", "contact_lawyer"),
                ("🔄 Начать заново", "preview_calc_start"),
            ),
        )
        return

    await state.update_data(unique_object=(callback.data == "preview_unique_yes"))
    await _show_result(callback, state, db)


@router.callback_query(lambda c: c.data == "preview_calc_recalculate")
async def preview_recalculate(callback: CallbackQuery, state: FSMContext, db):
    await _show_result(callback, state, db)


async def _existing_materialized_case(db, *, client_id: int, preview_id: str):
    request = (
        await db.execute(
            select(CaseCreationRequest).where(
                CaseCreationRequest.client_id == int(client_id),
                CaseCreationRequest.operation_key == _preview_operation_key(preview_id),
            )
        )
    ).scalar_one_or_none()
    if request is None:
        return None
    return await db.get(Case, int(request.case_id))


async def _present_saved(callback: CallbackQuery, state: FSMContext, db, case, result) -> None:
    case_id = int(case.id)
    buttons: list[tuple[str, str]] = []
    if int(result.delay_days or 0) > 0 and Decimal(result.penalty_amount or 0) > 0:
        buttons.append(
            ("⚖️ Полное ведение дела", f"calc_continue_m1:v2:{case_id}")
        )
        if bool(settings.self_filing_new_sales_enabled):
            buttons.append(
                (
                    "📄 Подготовить пакет — в суд пойду сам",
                    f"calc_self_filing:v2:{case_id}",
                )
            )
    buttons.extend(
        [
            ("💬 Перейти к консультации", f"calc_to_m2:v2:{case_id}"),
            ("🔎 Основания и детализация", f"calc_details:v2:{case_id}"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    # Commit first. If PostgreSQL rejects the transaction, the unsaved preview
    # remains in FSM and the client can retry instead of losing the draft.
    await db.commit()
    await state.clear()
    await _safe_edit(
        callback,
        "✅ Расчёт сохранён в отдельное обращение. Теперь он доступен в «Моём деле».\n\n"
        + format_calculation_result(result),
        reply_markup=one(*buttons),
    )


@router.callback_query(
    lambda c: c.data == "preview_calc_save"
    or str(c.data or "").startswith("preview_calc_save:v2:")
)
async def preview_save(callback: CallbackQuery, state: FSMContext, db):
    data = _clean_preview_data(await state.get_data())
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)

    callback_preview_id = _preview_id_from_callback(callback.data)
    state_preview_id = _valid_preview_id(data.get("preview_materialization_id"))
    preview_id = callback_preview_id or state_preview_id
    if preview_id is None:
        await db.rollback()
        await _safe_edit(
            callback,
            "Этот временный расчёт уже завершился или относится к старому экрану. "
            "Новое дело не создано.",
            reply_markup=_exit_buttons(
                ("🧮 Начать быстрый расчёт", "preview_calc_start"),
            ),
        )
        return

    # The stable preview id is carried by the button itself. This makes two
    # distinct Telegram callback ids from one double tap converge to the same
    # CaseCreationRequest instead of creating two Cases.
    existing_case = await _existing_materialized_case(
        db,
        client_id=int(user.id),
        preview_id=preview_id,
    )
    if existing_case is not None:
        result = await CalculatorService(db).result_for_case(case_id=int(existing_case.id))
        if result is not None:
            await _present_saved(callback, state, db, existing_case, result)
            return

    if state_preview_id != preview_id:
        await db.rollback()
        await _safe_edit(
            callback,
            "Эта кнопка относится к другому предварительному расчёту. "
            "Действие не выполнено и новое дело не создано.",
            reply_markup=_exit_buttons(
                ("🧮 Открыть текущий быстрый расчёт заново", "preview_calc_start"),
            ),
        )
        return

    if not _ready(data):
        await db.rollback()
        await _safe_edit(
            callback,
            "👀 Временный расчёт уже завершился или был очищен. "
            "Новое дело не создано. Запустите быстрый расчёт снова.",
            reply_markup=_exit_buttons(("🧮 Начать быстрый расчёт", "preview_calc_start")),
        )
        return

    preview_date = str(data.get("preview_calculation_date") or "")
    preview_key = str(data.get("preview_rule_revision_key") or "")
    preview_sha = str(data.get("preview_rule_snapshot_sha256") or "")
    if not preview_date or not preview_key or not preview_sha:
        await db.rollback()
        await _safe_edit(
            callback,
            "Предварительный результат устарел и не может быть материализован без проверки. "
            "Дело не создано.",
            reply_markup=_exit_buttons(("🔄 Пересчитать", "preview_calc_recalculate")),
        )
        return

    try:
        current_revision = await CalculationRuleRevisionService(db).resolve(
            calculation_date=date.fromisoformat(preview_date)
        )
        if (
            str(current_revision.revision_key) != preview_key
            or str(current_revision.rules_sha256) != preview_sha
        ):
            await db.rollback()
            await _safe_edit(
                callback,
                "⚠️ После предварительного просмотра изменилась опубликованная редакция "
                "юридических правил. Чтобы не сохранять сумму на другой нормативной базе, "
                "нужно пересчитать. Дело не создано.",
                reply_markup=_exit_buttons(("🔄 Пересчитать", "preview_calc_recalculate")),
            )
            return

        case = await ctx.case_service.create_case_for_operation(
            client=user,
            operation_key=_preview_operation_key(preview_id),
            purpose="calculator_preview_save",
            status=CaseStatus.CALCULATOR_STARTED,
            title="Обращение по ДДУ",
        )

        intake_service = CalculationIntakeService(db)
        intake = await intake_service.get(case_id=int(case.id))
        if intake is None:
            intake = await intake_service.sync_from_draft(
                case_id=int(case.id),
                data=data,
                today=date.fromisoformat(preview_date),
            )
        elif str(intake.status) != INTAKE_COMPLETED:
            await intake_service.sync_from_draft(
                case_id=int(case.id),
                data=data,
                today=date.fromisoformat(preview_date),
            )

        result = await CalculatorService(db).calculate_and_save(
            case=case,
            contract_price=Decimal(str(data["contract_price"])),
            planned_transfer_date=date.fromisoformat(str(data["planned_transfer_date"])),
            calculation_date=date.fromisoformat(preview_date),
            object_transferred=bool(data["object_transferred"]),
            actual_transfer_date=(
                date.fromisoformat(str(data["actual_transfer_date"]))
                if data.get("actual_transfer_date")
                else None
            ),
            client_type=str(data["client_type"]),
            unique_object=bool(data["unique_object"]),
            manual_review_flags=tuple(data.get("manual_review_flags") or ()),
            expected_rule_revision_key=preview_key,
            expected_rule_snapshot_sha256=preview_sha,
        )
    except Exception:
        await db.rollback()
        logger.exception("Preview materialization failed")
        await _safe_edit(
            callback,
            "⚠️ Не удалось надёжно сохранить предварительный расчёт. "
            "Постоянный результат не подтверждён. Временные ответы пока остаются на этом экране.",
            reply_markup=_exit_buttons(
                ("🔄 Повторить сохранение", "preview_calc_save"),
                ("📁 Мои обращения", "my_cases_open"),
            ),
        )
        return

    await _present_saved(callback, state, db, case, result)


__all__ = ["router"]
