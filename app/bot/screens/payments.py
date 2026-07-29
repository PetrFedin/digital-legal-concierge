from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.config import settings
from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment

router = Router()


def money(value):
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def fake_payments_enabled() -> bool:
    return settings.payment_provider == "fake" and (
        settings.app_env in {"local", "test"} or settings.demo_mode
    )


def payment_keyboard(payment: Payment):
    keyboard = InlineKeyboardBuilder()
    if payment.payment_url and payment.status in {
        PaymentStatus.PENDING,
        PaymentStatus.WAITING_CONFIRMATION,
    }:
        keyboard.button(
            text="Перейти к оплате",
            url=payment.payment_url,
        )
    if fake_payments_enabled() and payment.status in {
        PaymentStatus.PENDING,
        PaymentStatus.WAITING_CONFIRMATION,
    }:
        keyboard.button(
            text="✅ DEV подтвердить оплату",
            callback_data=f"pay_fake_success:{payment.id}",
        )
    keyboard.button(text="📁 Мое дело", callback_data="my_case_open")
    keyboard.adjust(1)
    return keyboard.as_markup()


async def get_owned_payment(
    callback: CallbackQuery,
    db,
    payment_id: int,
):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    payment = await db.get(Payment, payment_id)
    if not payment:
        await callback.answer("Платёж не найден.", show_alert=True)
        return None, None
    case = (
        await db.execute(
            select(Case).where(
                Case.id == payment.case_id,
                Case.client_id == user.id,
            )
        )
    ).scalars().first()
    if not case:
        await callback.answer("Этот платёж вам недоступен.", show_alert=True)
        return None, None
    return payment, case


@router.callback_query(lambda c: c.data == "payments_open")
async def payments(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    payments_list = (
        await PaymentService(db).list_case_payments(case.id)
        if case
        else []
    )
    text = "💳 Оплаты\n\n" + (
        "Пока нет выставленных платежей."
        if not payments_list
        else "\n".join(
            [
                f"#{payment.id} {payment.title}: "
                f"{money(payment.amount)} — {payment.status}"
                for payment in payments_list
            ]
        )
    )
    items = [
        (f"Открыть оплату #{payment.id}", f"pay_open:{payment.id}")
        for payment in payments_list
    ]
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
    if not case:
        await callback.answer(
            "Сначала выберите дату и время консультации.",
            show_alert=True,
        )
        return

    service = PaymentService(db)
    try:
        payment = await service.get_or_create_payment(
            case=case,
            payment_code=code,
        )
        payment = await service.create_payment_link(payment)
        await db.commit()
    except (SlotUnavailableError, ValueError) as error:
        await db.rollback()
        if code == PaymentCode.M2_CONSULTATION_PAYMENT:
            await callback.message.edit_text(
                f"⏳ {error}\n\nВыберите новое свободное время.",
                reply_markup=one(
                    ("📅 Выбрать дату и время", "consult_booking_start"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
        else:
            await callback.answer(str(error), show_alert=True)
        return
    except RuntimeError:
        await db.rollback()
        await callback.answer(
            "Платёжный сервис временно недоступен.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        f"💳 {payment.title}\n\nСумма: {money(payment.amount)}\n\n"
        "После подтверждения оплаты слот станет окончательно вашим. "
        "Не используйте эту ссылку после выбора другого времени.",
        reply_markup=payment_keyboard(payment),
    )


@router.callback_query(lambda c: c.data.startswith("pay_open:"))
async def open_payment(callback: CallbackQuery, db):
    payment_id = int(callback.data.split(":", 1)[1])
    payment, _case = await get_owned_payment(callback, db, payment_id)
    if not payment:
        return
    review_note = (
        "\n\n⚠️ Деньги получены, но запись проверяет администратор."
        if payment.status == PaymentStatus.PAID_REVIEW
        else ""
    )
    await callback.message.edit_text(
        f"💳 {payment.title}\n"
        f"Сумма: {money(payment.amount)}\n"
        f"Статус: {payment.status}{review_note}",
        reply_markup=payment_keyboard(payment),
    )


@router.callback_query(lambda c: c.data.startswith("pay_fake_success:"))
async def fake(callback: CallbackQuery, db):
    if not fake_payments_enabled():
        await callback.answer("DEV-оплата отключена.", show_alert=True)
        return

    payment_id = int(callback.data.split(":", 1)[1])
    payment, case = await get_owned_payment(callback, db, payment_id)
    if not payment or not case:
        return
    if payment.provider not in {None, "fake"}:
        await callback.answer(
            "Этот платёж создан другим провайдером.",
            show_alert=True,
        )
        return

    await PaymentWebhookService(db).process_successful_payment(
        payment=payment,
        case=case,
        provider_payload={"dev": True},
    )
    await db.commit()

    if payment.status == PaymentStatus.PAID_REVIEW:
        await callback.message.edit_text(
            "⚠️ Оплата получена, но резерв времени уже изменился или истёк. "
            "Администратор проверит платёж и свяжется с вами.",
            reply_markup=one(
                ("📅 Выбрать новое время", "consult_booking_start"),
                ("📁 Мое дело", "my_case_open"),
            ),
        )
        return

    if payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
        await callback.message.edit_text(
            "✅ Оплата подтверждена, консультация забронирована.\n\n"
            "Теперь выберите, к какому делу относится встреча, "
            "и напишите конкретный вопрос для юриста.",
            reply_markup=one(
                ("📝 Указать дело и вопрос", "consult_subject_start"),
                ("👨‍⚖ Открыть запись", "consultation_booked_open"),
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
