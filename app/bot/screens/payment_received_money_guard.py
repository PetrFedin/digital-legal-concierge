from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import payment_archive_guard, payments
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.payment import Payment

router = Router()

_UNRESOLVED_RECEIVED_M2 = (
    PaymentStatus.PAID_REVIEW.value,
    PaymentStatus.REFUND_PENDING.value,
    PaymentStatus.REFUND_DECLINED.value,
)
_ACTIVE_LINK = (
    PaymentStatus.PENDING.value,
    PaymentStatus.WAITING_CONFIRMATION.value,
)


async def _unresolved_received_m2(callback: CallbackQuery, db):
    """Return scalar payment context only; never retain ORM state across rollback."""

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case is None or str(case.route or "").upper() != RouteCode.M2.value:
        return None

    case_id = int(case.id)
    payment = (
        await db.execute(
            select(Payment)
            .where(
                Payment.case_id == case_id,
                Payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT.value,
                Payment.status.in_(_UNRESOLVED_RECEIVED_M2),
            )
            .order_by(Payment.updated_at.desc(), Payment.id.desc())
            .limit(1)
        )
    ).scalars().first()
    if payment is None:
        return None
    return {
        "case_id": case_id,
        "payment_id": int(payment.id),
        "status": str(payment.status),
    }


def _status_text(status: str) -> str:
    return {
        PaymentStatus.PAID_REVIEW.value: "деньги получены и проверяются командой",
        PaymentStatus.REFUND_PENDING.value: "по предыдущему платежу обрабатывается возврат",
        PaymentStatus.REFUND_DECLINED.value: "решение по возврату требует дальнейшей финансовой сверки",
    }.get(status, "предыдущий платёж ещё не разобран командой")


def _review_keyboard():
    return one(
        ("💳 Посмотреть оплаты", "payments_open"),
        ("✉️ Написать команде", "message_create"),
        ("📁 Моё дело", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    )


async def _render_no_second_charge(callback: CallbackQuery, conflict: dict) -> None:
    payment_id = int(conflict["payment_id"])
    status = str(conflict["status"])
    await callback.message.edit_text(
        "💳 Повторная оплата сейчас не нужна\n\n"
        f"По платежу #{payment_id} {_status_text(status)}. "
        "Бот не создаст вторую ссылку и не попросит выбрать другой слот только ради повторной оплаты.\n\n"
        "Сначала команда должна завершить сверку предыдущих денег. Данные вопроса и документы сохранены.",
        reply_markup=_review_keyboard(),
    )


@router.callback_query(lambda c: c.data == "payments_open")
async def unresolved_money_payment_list(callback: CallbackQuery, db):
    conflict = await _unresolved_received_m2(callback, db)
    if conflict is None:
        await payment_archive_guard.guard_active_m2_payment_list(callback, db)
        return

    # Do not ask PaymentService to materialize another current M2 obligation
    # while already received money is unresolved. The canonical read-only list
    # is still useful and shows the exact financial status to the client.
    await db.rollback()
    await payments.payments(callback, db)


@router.callback_query(lambda c: c.data == "consult_pay")
async def unresolved_money_consult_pay(callback: CallbackQuery, db):
    conflict = await _unresolved_received_m2(callback, db)
    if conflict is None:
        await payment_archive_guard.legacy_consult_pay_is_navigation(callback, db)
        return

    await db.rollback()
    await _render_no_second_charge(callback, conflict)


@router.callback_query(
    lambda c: bool(c.data) and c.data.startswith("pay_open:")
)
async def unresolved_money_old_link(callback: CallbackQuery, db):
    """Hide any still-active M2 provider link while earlier money is unresolved."""

    conflict = await _unresolved_received_m2(callback, db)
    if conflict is None:
        await payment_archive_guard.guard_archived_payment_open(callback, db)
        return

    try:
        requested_id = int(str(callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError, IndexError):
        await payment_archive_guard.guard_archived_payment_open(callback, db)
        return

    link = (
        await db.execute(
            select(Payment.id)
            .where(
                Payment.id == requested_id,
                Payment.case_id == int(conflict["case_id"]),
                Payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT.value,
                Payment.status.in_(_ACTIVE_LINK),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if link is None:
        await payment_archive_guard.guard_archived_payment_open(callback, db)
        return

    await db.rollback()
    await _render_no_second_charge(callback, conflict)


__all__ = ["router"]
