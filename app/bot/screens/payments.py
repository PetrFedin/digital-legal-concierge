from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService

router = Router()


def money(value):
    return f"{value:,.2f}".replace(",", " ") + " ₽"


@router.callback_query(lambda c: c.data == "payments_open")
async def payments(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    payments_list = await PaymentService(db).list_case_payments(case.id) if case else []
    text = "💳 Оплаты\n\n" + (
        "Пока нет выставленных платежей."
        if not payments_list
        else "\n".join(
            [f"#{payment.id} {payment.title}: {money(payment.amount)} — {payment.status}" for payment in payments_list]
        )
    )
    items = [(f"Открыть оплату #{payment.id}", f"pay_open:{payment.id}") for payment in payments_list]
    items += [("📁 Мое дело", "my_case_open")]
    await callback.message.edit_text(text, reply_markup=one(*items))


@router.callback_query(lambda c: c.data == "pay_start_30000")
async def pay_30000(callback: CallbackQuery, db):
    await start_payment(callback, db, PaymentCode.M1_INITIAL_PAYMENT)


@router.callback_query(lambda c: c.data == "consult_pay")
async def consult_pay(callback: CallbackQuery, db):
    await start_payment(callback, db, PaymentCode.M2_CONSULTATION_PAYMENT)


async def start_payment(callback: CallbackQuery, db, code):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    service = PaymentService(db)
    payment = await service.get_or_create_payment(case=case, payment_code=code)
    payment = await service.create_payment_link(payment)
    await db.commit()
    await callback.message.edit_text(
        f"💳 {payment.title}\n\nСумма: {money(payment.amount)}",
        reply_markup=one(
            ("Перейти к оплате", "noop"),
            ("✅ DEV подтвердить оплату", f"pay_fake_success:{payment.id}"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("pay_open:"))
async def open_payment(callback: CallbackQuery, db):
    payment = await PaymentService(db).get_payment(int(callback.data.split(":")[1]))
    await callback.message.edit_text(
        f"💳 {payment.title}\nСумма: {money(payment.amount)}\nСтатус: {payment.status}",
        reply_markup=one(("✅ DEV подтвердить оплату", f"pay_fake_success:{payment.id}"), ("📁 Мое дело", "my_case_open")),
    )


@router.callback_query(lambda c: c.data.startswith("pay_fake_success:"))
async def fake(callback: CallbackQuery, db):
    payment = await PaymentService(db).get_payment(int(callback.data.split(":")[1]))
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    await PaymentWebhookService(db).process_successful_payment(
        payment=payment,
        case=case,
        provider_payload={"dev": True},
    )
    await db.commit()
    await callback.message.edit_text(
        "✅ Оплата подтверждена. Следующий этап открыт автоматически.",
        reply_markup=one(("📁 Мое дело", "my_case_open"), ("🏠 Главная", "nav_home")),
    )
