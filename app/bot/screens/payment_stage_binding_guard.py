from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.case_callback_scope import (
    bound_case_callback,
    callback_matches_action,
    resolve_case_callback_scope,
)
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import payments as payment_screen
from app.domain.payments.mode import payments_disabled
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case

router = Router()
logger = logging.getLogger(__name__)

_STAGE_BY_ACTION = {
    "pay_start_30000": (
        CaseStatus.M1_WAITING_PAYMENT_30000,
        PaymentCode.M1_INITIAL_PAYMENT.value,
        "Первый платёж 30 000 ₽",
    ),
    "pay_court_70000": (
        CaseStatus.M1_WAITING_PAYMENT_70000,
        PaymentCode.M1_COURT_PAYMENT.value,
        "Второй платёж 70 000 ₽",
    ),
    "pay_success_fee": (
        CaseStatus.M1_WAITING_SUCCESS_FEE,
        PaymentCode.M1_SUCCESS_FEE.value,
        "Финальный процент",
    ),
}
_STAGE_BY_CODE = {str(item[1]): (action, *item) for action, item in _STAGE_BY_ACTION.items()}
_OPENABLE_PAYMENT_STATUSES = {
    PaymentStatus.PENDING.value,
    PaymentStatus.WAITING_CONFIRMATION.value,
}


def _status(case: Case) -> CaseStatus | None:
    try:
        return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


def _stage_action(value: str | None) -> str | None:
    for action in _STAGE_BY_ACTION:
        if callback_matches_action(value, action):
            return action
    return None


def _bound(case_id: int, payment_code: str) -> str:
    return f"pay_stage:v2:{int(case_id)}:{payment_code}"


def _parse_bound(value: str) -> tuple[int, str] | None:
    prefix = "pay_stage:v2:"
    if not str(value or "").startswith(prefix):
        return None
    tail = str(value)[len(prefix) :]
    case_raw, separator, payment_code = tail.partition(":")
    if not separator or payment_code not in _STAGE_BY_CODE:
        return None
    try:
        case_id = int(case_raw)
    except ValueError:
        return None
    if case_id <= 0:
        return None
    return case_id, payment_code


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram не обновил экран безопасного платежа")


async def _current_case(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


def _exact_payment_presentation(*, payment, case_id: int, case_number: str, title: str):
    """Snapshot all ORM-backed payment presentation before transaction release."""

    payment_status = str(payment.status)
    status_label = payment_screen.client_payment_status_label(payment)
    status_note = payment_screen.client_payment_status_note(payment)
    amount_text = payment_screen.money(payment.amount)
    lines = [
        f"💳 {title}",
        f"Обращение № {case_number}",
        "",
        "СЕЙЧАС",
        f"Сумма: {amount_text}",
        f"Статус: {status_label}",
    ]
    if status_note:
        lines.extend(["", status_note])

    if payments_disabled() and payment_status in _OPENABLE_PAYMENT_STATUSES:
        lines.extend(
            [
                "",
                "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ",
                "Онлайн-оплата сейчас отключена. Команда изменит этап только после проверки фактического поступления.",
            ]
        )
        markup = one(
            ("💳 Все оплаты", "payments_open"),
            (
                "✉️ Написать команде",
                bound_case_callback("message_create", int(case_id)),
            ),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif payment_status in _OPENABLE_PAYMENT_STATUSES:
        lines.extend(
            [
                "",
                "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ",
                "Откройте существующий платёж по ссылке ниже. Переход по ссылке сам по себе не меняет юридический этап; следующий этап откроется только после серверного подтверждения фактической оплаты.",
            ]
        )
        # The keyboard reads payment_url/status/id. Build it while ORM state is
        # still live; Telegram network I/O happens only after the DB boundary.
        markup = payment_screen.payment_keyboard(payment)
    else:
        lines.extend(
            [
                "",
                "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ",
                "Этот платёж уже не находится в состоянии ожидания оплаты. Откройте актуальную платёжную историю; повторное обязательство не создаётся.",
            ]
        )
        markup = one(
            ("💳 Все оплаты", "payments_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    return "\n".join(lines), markup


@router.callback_query(lambda c: _stage_action(c.data) is not None)
async def stage_payment_is_exact_confirmation_only(callback: CallbackQuery, db):
    """Own raw and v2 M1 payment entry before legacy payment handlers.

    A client payment button may open only a financial obligation already created
    by the authoritative business transition: contract confirmation, court
    decision, or lawyer-recorded recovery. It must never synthesize the missing
    obligation or advance M1_MONEY_RECEIVED into the success-fee stage.

    Historical raw callbacks are accepted only when their bot-rendered Case
    context is unambiguous. Fresh v2 callbacks are tied to the selected exact
    Case. Both forms are converted into the same explicit ``pay_stage`` screen.
    """

    action = _stage_action(callback.data)
    if action is None:
        return
    expected_status, payment_code, title = _STAGE_BY_ACTION[action]
    scope = await resolve_case_callback_scope(
        callback,
        db,
        action=action,
        allow_legacy_message_case_context=True,
    )
    if scope is None:
        return
    case = scope.case
    if case is None:
        await db.rollback()
        await _safe_edit(
            callback,
            "Активное дело не найдено. Эта кнопка оплаты ничего не изменила и новый платёж не создавался.",
            reply_markup=one(
                ("💳 Все оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    current_status = _status(case)
    if current_status != expected_status:
        await db.rollback()
        if action == "pay_success_fee" and current_status == CaseStatus.M1_MONEY_RECEIVED:
            message = (
                "Фактическое взыскание уже отмечено, но финальное платёжное обязательство ещё не открыто ответственным юристом. "
                "Клиентская кнопка не может создать его или перевести дело дальше."
            )
        else:
            message = (
                "Эта кнопка оплаты относится к другому или уже завершённому этапу дела. Финансовый статус и юридический этап не изменены."
            )
        case_id = int(case.id)
        await _safe_edit(
            callback,
            message,
            reply_markup=one(
                ("💳 Актуальные оплаты", "payments_open"),
                ("📁 Открыть текущее дело", "my_case_open"),
                (
                    "✉️ Написать команде",
                    bound_case_callback("message_create", case_id),
                ),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    await db.rollback()
    await _safe_edit(
        callback,
        f"💳 {title}\n"
        f"Обращение № {case_number}\n\n"
        "СЕЙЧАС\n"
        "Платёжный этап подтверждён текущим состоянием дела. Нажатие этой кнопки не создаёт новое финансовое обязательство и не считает деньги полученными.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Откройте уже существующий платёж именно этого этапа. Если обязательство отсутствует, система остановится и передаст проблему в рабочий контур вместо автоматического восстановления деньгами клиента.",
        reply_markup=one(
            ("💳 Открыть платёж", _bound(case_id, payment_code)),
            ("💳 Все оплаты", "payments_open"),
            (
                "✉️ Написать команде",
                bound_case_callback("message_create", case_id),
            ),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: bool(c.data) and c.data.startswith("pay_stage:v2:"))
async def open_exact_stage_payment(callback: CallbackQuery, db):
    parsed = _parse_bound(str(callback.data or ""))
    if parsed is None:
        await db.rollback()
        await _safe_edit(
            callback,
            "Некорректная платёжная кнопка. Ничего не изменено.",
            reply_markup=one(
                ("💳 Все оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    case_id, payment_code = parsed
    _action, expected_status, _code, title = _STAGE_BY_CODE[payment_code]
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    committed_provider_link = False
    try:
        case = (
            await db.execute(
                select(Case)
                .where(Case.id == int(case_id), Case.client_id == int(user.id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if case is None:
            raise LookupError("Дело из этого сообщения не найдено или недоступно")
        active = await ctx.case_service.get_active_case_for_user(user.id)
        if active is None or int(active.id) != int(case.id):
            raise ValueError("Это уже не текущее активное дело")
        if _status(case) != expected_status:
            raise ValueError("Платёжный этап уже изменился")

        case_number = str(case.case_number)
        service = PaymentService(db)
        case_payments = await service.list_case_payments(case.id)
        payment = next(
            (
                item
                for item in reversed(case_payments)
                if str(item.payment_code) == str(payment_code)
            ),
            None,
        )
        if payment is None:
            raise ValueError(
                "Для этого этапа нет платёжного обязательства. Новый платёж из Telegram автоматически не создавался"
            )

        payment_status = str(payment.status)
        if (
            payment_status in _OPENABLE_PAYMENT_STATUSES
            and not payments_disabled()
            and not payment.payment_url
        ):
            payment = await service.create_payment_link(payment)
            # The provider result is a durable financial write. Snapshot every
            # presentation value, including the URL keyboard, before commit so
            # the client view never depends on an ORM instance crossing the
            # transaction boundary.
            text, markup = _exact_payment_presentation(
                payment=payment,
                case_id=case_id,
                case_number=case_number,
                title=title,
            )
            await db.commit()
            committed_provider_link = True
        else:
            text, markup = _exact_payment_presentation(
                payment=payment,
                case_id=case_id,
                case_number=case_number,
                title=title,
            )
            await db.rollback()
    except (LookupError, ValueError, RuntimeError) as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"💳 ОПЛАТА НЕ ОТКРЫТА\n\n"
            f"СЕЙЧАС\n{error}.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Откройте актуальные оплаты или сообщите команде. Юридический этап и платёжный статус не были изменены; отсутствующее обязательство автоматически не создаётся.",
            reply_markup=one(
                ("💳 Актуальные оплаты", "payments_open"),
                (
                    "✉️ Написать команде",
                    bound_case_callback("message_create", case_id),
                ),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Bound payment stage open failed: case=%s code=%s", case_id, payment_code)
        await _safe_edit(
            callback,
            "💳 ОПЛАТА ВРЕМЕННО НЕДОСТУПНА\n\n"
            "СЕЙЧАС\nБезопасно открыть платёж не удалось. Новый платёж не создан, юридический этап не изменён.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Повторите через раздел «Оплаты» или напишите команде.",
            reply_markup=one(
                ("💳 Все оплаты", "payments_open"),
                (
                    "✉️ Написать команде",
                    bound_case_callback("message_create", case_id),
                ),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if committed_provider_link:
        logger.info(
            "Provider link committed for existing M1 payment: case=%s code=%s",
            case_id,
            payment_code,
        )
    await _safe_edit(callback, text, reply_markup=markup)


__all__ = ["router"]
