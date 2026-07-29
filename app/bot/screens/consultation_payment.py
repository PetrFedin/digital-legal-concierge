from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.consultations.payment_lifecycle_service import (
    ConsultationPaymentLifecycleError,
    ConsultationPaymentLifecycleService,
)
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus


router = Router()
logger = logging.getLogger(__name__)


PAYMENT_STATUS_TITLES = {
    PaymentStatus.PENDING.value: "Ожидает оплаты",
    PaymentStatus.WAITING_CONFIRMATION.value: "Проверяем поступление",
    PaymentStatus.PAID.value: "Оплачено",
    PaymentStatus.FAILED.value: "Оплата не завершена",
    PaymentStatus.CANCELLED.value: "Отменено",
    PaymentStatus.REFUNDED.value: "Возвращено",
    PaymentStatus.EXPIRED.value: "Ссылка истекла",
}


def _value(value) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


def _money(value) -> str:
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def _payment_text(payment) -> str:
    status = _value(payment.status)
    status_title = (
        "Требуется проверка сотрудником"
        if getattr(payment, "manual_review_required", False)
        else PAYMENT_STATUS_TITLES.get(status, "Статус уточняется")
    )
    if getattr(payment, "manual_review_required", False):
        explanation = (
            "Платёж сохранён для безопасной ручной проверки. Повторно платить "
            "не нужно, пока сотрудник не уточнит статус."
        )
    elif status == PaymentStatus.WAITING_CONFIRMATION.value:
        explanation = (
            "Платёжная ссылка создана. После подтверждения провайдера выбранное "
            "время будет закреплено и передано юристу на подтверждение."
        )
    elif status == PaymentStatus.PENDING.value:
        explanation = "Счёт подготовлен и ожидает создания платёжной ссылки."
    elif status == PaymentStatus.PAID.value:
        explanation = "Оплата подтверждена. Повторно оплачивать не нужно."
    else:
        explanation = "Откройте актуальный шаг в «Моём деле»."
    return (
        "💳 Оплата юридической консультации\n"
        "━━━━━━━━━━━━━━━━\n"
        f"Сумма: {_money(payment.amount)}\n"
        f"Статус: {status_title}\n\n"
        f"{explanation}\n\n"
        "Не создавайте повторную оплату, если деньги уже списаны."
    )


def _payment_markup(payment):
    status = _value(payment.status)
    buttons = []
    if (
        status in {
            PaymentStatus.PENDING.value,
            PaymentStatus.WAITING_CONFIRMATION.value,
        }
        and payment.payment_url
        and not getattr(payment, "manual_review_required", False)
    ):
        buttons.append(("💳 Перейти к безопасной оплате", payment.payment_url))
    if getattr(payment, "manual_review_required", False):
        buttons.append(("💬 Уточнить у менеджера", "contact_lawyer"))
    buttons.extend(
        [
            ("🕐 Выбранное время", "consult_slot_reserved_open"),
            ("💳 Все оплаты", "payments_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    return one(*buttons)


async def _show_error(callback: CallbackQuery, text: str) -> None:
    await callback.message.edit_text(
        "⚠️ Не удалось продолжить оплату\n\n"
        f"{text}\n\n"
        "Если деньги уже списаны, не оплачивайте повторно.",
        reply_markup=one(
            ("🕐 Проверить выбранное время", "consult_slot_reserved_open"),
            ("📁 Моё дело", "my_case_open"),
            ("💬 Связаться с менеджером", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_pay")
async def consultation_payment(callback: CallbackQuery, db):
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        if user.is_blocked:
            raise ConsultationPaymentLifecycleError("Доступ клиента ограничен.")

        cases = await ctx.case_service.list_active_cases_for_user(
            user.id,
            route=RouteCode.M2,
        )
        if len(cases) != 1:
            raise ConsultationPaymentLifecycleError(
                "Не удалось однозначно определить активное консультационное дело."
            )
        case = cases[0]
        if case.client_id != user.id:
            raise ConsultationPaymentLifecycleError(
                "Консультационное дело не принадлежит текущему клиенту."
            )

        await ConsultationPaymentLifecycleService(db).prepare_payment(
            case=case,
            client_id=user.id,
            actor_type="client",
            source="telegram",
        )
        service = PaymentService(db)
        payment = await service.get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        )
        payment = await service.create_payment_link(payment)
        await db.commit()
    except ConsultationPaymentLifecycleError as exc:
        await db.rollback()
        await _show_error(callback, str(exc))
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while preparing M2 consultation payment")
        await _show_error(
            callback,
            "Не удалось подготовить оплату. Проверьте резерв и повторите попытку.",
        )
        return

    try:
        await callback.message.edit_text(
            _payment_text(payment),
            reply_markup=_payment_markup(payment),
        )
    except Exception:
        logger.exception(
            "Telegram response failed after M2 payment commit",
            extra={"payment_id": payment.id, "case_id": case.id},
        )
