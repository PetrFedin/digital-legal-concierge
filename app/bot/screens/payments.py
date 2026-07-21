import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.config import settings
from app.domain.consultations.payment_lifecycle_service import (
    ConsultationPaymentLifecycleError,
    ConsultationPaymentLifecycleService,
)
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.payment_statuses import PaymentStatus

router = Router()
logger = logging.getLogger(__name__)


def money(value):
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def _payment_buttons(payment):
    items = []
    if payment.status != PaymentStatus.PAID and payment.payment_url:
        items.append(("Перейти к оплате", payment.payment_url))
    if settings.app_env == "local" and payment.status != PaymentStatus.PAID:
        items.append(
            ("✅ DEV подтвердить оплату", f"pay_fake_success:{payment.id}")
        )
    items.append(("📁 Мое дело", "my_case_open"))
    return one(*items)


async def _load_owned_case(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case is None or case.client_id != user.id:
        return user, None
    return user, case


async def _show_payment_error(callback: CallbackQuery, text: str) -> None:
    await callback.message.edit_text(
        text,
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "payments_open")
async def payments(callback: CallbackQuery, db):
    _, case = await _load_owned_case(callback, db)
    payments_list = (
        await PaymentService(db).list_case_payments(case.id) if case else []
    )
    text = "💳 Оплаты\n\n" + (
        "Пока нет выставленных платежей."
        if not payments_list
        else "\n".join(
            [
                f"#{payment.id} {payment.title}: {money(payment.amount)} — "
                f"{payment.status}"
                for payment in payments_list
            ]
        )
    )
    items = [
        (f"Открыть оплату #{payment.id}", f"pay_open:{payment.id}")
        for payment in payments_list
    ]
    items.append(("📁 Мое дело", "my_case_open"))
    await callback.message.edit_text(text, reply_markup=one(*items))


@router.callback_query(lambda c: c.data == "pay_start_30000")
async def pay_30000(callback: CallbackQuery, db):
    await start_payment(callback, db, PaymentCode.M1_INITIAL_PAYMENT)


@router.callback_query(lambda c: c.data == "consult_pay")
async def consult_pay(callback: CallbackQuery, db):
    user, case = await _load_owned_case(callback, db)
    if case is None:
        await _show_payment_error(
            callback,
            "Активное дело не найдено. Откройте «Мое дело» и продолжите с текущего шага.",
        )
        return

    try:
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
        await _show_payment_error(callback, str(exc))
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while preparing Telegram M2 payment")
        await _show_payment_error(
            callback,
            "Не удалось подготовить оплату. Проверьте выбранное время и попробуйте ещё раз.",
        )
        return

    try:
        await callback.message.edit_text(
            f"💳 {payment.title}\n\n"
            f"Сумма: {money(payment.amount)}\n\n"
            "Оплатите консультацию до окончания срока удержания слота. "
            "После оплаты потребуется подтверждение назначенного юриста.",
            reply_markup=_payment_buttons(payment),
        )
    except Exception:
        logger.exception(
            "Telegram response failed after M2 payment preparation commit",
            extra={"payment_id": payment.id, "case_id": case.id},
        )


async def start_payment(callback: CallbackQuery, db, code):
    if code == PaymentCode.M2_CONSULTATION_PAYMENT:
        await consult_pay(callback, db)
        return

    _, case = await _load_owned_case(callback, db)
    if case is None:
        await callback.answer("Активное дело не найдено.", show_alert=True)
        return

    try:
        service = PaymentService(db)
        payment = await service.get_or_create_payment(
            case=case,
            payment_code=code,
        )
        payment = await service.create_payment_link(payment)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while preparing Telegram payment")
        await _show_payment_error(
            callback,
            "Не удалось подготовить оплату. Попробуйте ещё раз немного позже.",
        )
        return

    await callback.message.edit_text(
        f"💳 {payment.title}\n\nСумма: {money(payment.amount)}",
        reply_markup=_payment_buttons(payment),
    )


@router.callback_query(lambda c: c.data.startswith("pay_open:"))
async def open_payment(callback: CallbackQuery, db):
    try:
        payment_id = int(callback.data.split(":", 1)[1])
        if payment_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        await callback.answer("Некорректный номер платежа.", show_alert=True)
        return

    _, case = await _load_owned_case(callback, db)
    payment = await PaymentService(db).get_payment(payment_id)
    if case is None or payment is None or payment.case_id != case.id:
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    await callback.message.edit_text(
        f"💳 {payment.title}\n"
        f"Сумма: {money(payment.amount)}\n"
        f"Статус: {payment.status}",
        reply_markup=_payment_buttons(payment),
    )


@router.callback_query(lambda c: c.data.startswith("pay_fake_success:"))
async def fake(callback: CallbackQuery, db):
    if settings.app_env != "local":
        await callback.answer("DEV-подтверждение отключено.", show_alert=True)
        return

    try:
        payment_id = int(callback.data.split(":", 1)[1])
        if payment_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        await callback.answer("Некорректный номер платежа.", show_alert=True)
        return

    _, case = await _load_owned_case(callback, db)
    payment = await PaymentService(db).get_payment(payment_id)
    if case is None or payment is None or payment.case_id != case.id:
        await callback.answer("Платёж не найден.", show_alert=True)
        return

    try:
        await PaymentWebhookService(db).process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"dev": True, "source": "telegram"},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "Unexpected error while processing Telegram fake payment",
            extra={"payment_id": payment.id, "case_id": case.id},
        )
        await _show_payment_error(
            callback,
            "Не удалось обработать тестовую оплату. Платёж требует проверки.",
        )
        return

    if payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
        await callback.message.edit_text(
            "✅ Оплата получена.\n\n"
            "Выбранное время закреплено за вами. Теперь назначенный юрист "
            "должен подтвердить консультацию.",
            reply_markup=one(
                ("📁 Мое дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        "✅ Оплата подтверждена. Следующий этап открыт автоматически.",
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
