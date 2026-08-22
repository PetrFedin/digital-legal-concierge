import logging

from aiogram import Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from app.bot.case_callback_scope import (
    bound_case_callback,
    callback_matches_action,
    resolve_case_callback_scope,
)
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.config import settings
from app.domain.cases.client_case_scope import (
    active_or_latest_completed_m1_case_for_user,
    latest_completed_strict_m1_case_for_user,
)
from app.domain.consultations.consultation_intake import (
    ConsultationDescriptionRequired,
)
from app.domain.consultations.consultation_no_payment_booking import (
    ConsultationNoPaymentBookingService,
)
from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.payments.mode import payments_disabled
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment
from app.presentation_time import format_business_datetime

router = Router()
logger = logging.getLogger(__name__)

PAYMENT_STATUS_LABELS = {
    PaymentStatus.PENDING: "Ожидает оплаты",
    PaymentStatus.WAITING_CONFIRMATION: "Ожидает подтверждения",
    PaymentStatus.PAID: "Оплачено",
    PaymentStatus.PAID_REVIEW: "Получено, проверяется командой",
    PaymentStatus.REFUND_PENDING: "Возврат обрабатывается",
    PaymentStatus.REFUND_DECLINED: "Возврат отклонён",
    PaymentStatus.FAILED: "Оплата не прошла",
    PaymentStatus.CANCELLED: "Оплата отменена",
    PaymentStatus.REFUNDED: "Средства возвращены",
    PaymentStatus.EXPIRED: "Срок оплаты истёк",
}

M1_PAYMENT_EXPECTED_STATUSES = {
    PaymentCode.M1_INITIAL_PAYMENT: CaseStatus.M1_WAITING_PAYMENT_30000,
    PaymentCode.M1_COURT_PAYMENT: CaseStatus.M1_WAITING_PAYMENT_70000,
}
OFFLINE_M1_PAYMENT_CODES = {
    PaymentCode.M1_INITIAL_PAYMENT,
    PaymentCode.M1_COURT_PAYMENT,
    PaymentCode.M1_SUCCESS_FEE,
}


def money(value):
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def payment_status_label(value) -> str:
    try:
        status = PaymentStatus(str(value))
    except ValueError:
        return "Статус уточняется"
    return PAYMENT_STATUS_LABELS.get(status, "Статус уточняется")


def is_offline_m1_payment_waiting_for_team(payment: Payment) -> bool:
    """Identify a real offline M1 obligation, not merely a runtime provider toggle."""

    return bool(
        payments_disabled()
        and payment.payment_code in OFFLINE_M1_PAYMENT_CODES
        and payment.status
        in {
            PaymentStatus.PENDING,
            PaymentStatus.WAITING_CONFIRMATION,
        }
        and not payment.payment_url
        and not payment.provider
    )


def client_payment_status_label(payment: Payment) -> str:
    if is_offline_m1_payment_waiting_for_team(payment):
        return "Ожидает подтверждения командой"
    return payment_status_label(payment.status)


def client_payment_status_note(payment: Payment) -> str:
    if not is_offline_m1_payment_waiting_for_team(payment):
        return ""
    return (
        "Онлайн-оплата для этого этапа отключена. Новый платёж через бот создавать не нужно. "
        "Команда обновит статус после проверки фактического поступления денег."
    )


def payment_summary_line(payment: Payment) -> str:
    return (
        f"• {payment.title}\n"
        f"  {money(payment.amount)} · {client_payment_status_label(payment)}"
    )


def payment_action_label(payment: Payment) -> str:
    title = " ".join(str(payment.title or "Оплата").split())
    if len(title) > 48:
        title = title[:47].rstrip() + "…"
    return f"Открыть: {title}"


def _m1_payment_context_matches(case: Case, code: str) -> bool:
    expected = M1_PAYMENT_EXPECTED_STATUSES.get(code)
    if expected is None:
        return True
    return str(case.route or "") == RouteCode.M1.value and str(case.status) == str(expected)


def payment_review_keyboard(case: Case):
    """Safe recovery navigation while money is under manual reconciliation.

    A received payment under review must not expose actions that can reserve a
    different slot or create another payment before the team resolves the money.
    """

    items = []
    if (
        str(case.route or "") == RouteCode.M2.value
        and str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value
    ):
        items.append(("👨‍⚖ Текущая запись", "consultation_booked_open"))
    items.extend(
        [
            ("✉️ Написать команде", "message_create"),
            ("💳 Все оплаты", "payments_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    return one(*items)


async def _present_committed_callback(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup,
) -> None:
    """Present a durable payment result without turning Telegram UI failure into write failure."""

    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            return
        logger.warning("Не удалось обновить сообщение после сохранения оплаты: %s", error)
    except (TelegramNetworkError, TelegramServerError) as error:
        logger.warning("Telegram недоступен после сохранения оплаты: %s", error)

    try:
        await callback.message.answer(text, reply_markup=reply_markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError) as error:
        # The database transaction is already committed. A Telegram outage must
        # not be reported to the user as a failed payment write or trigger retry.
        logger.warning("Не удалось показать сохранённый результат оплаты: %s", error)


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
    keyboard.button(text="💳 Все оплаты", callback_data="payments_open")
    keyboard.button(text="📁 Моё дело", callback_data="my_case_open")
    keyboard.button(text="🏠 Главная", callback_data="nav_home")
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
    case, completed = await active_or_latest_completed_m1_case_for_user(
        db,
        case_service=ctx.case_service,
        user_id=user.id,
    )
    if not case:
        await callback.message.edit_text(
            "💳 Оплаты\n\nАктивного или завершённого дела нет. Платёжная история появится после создания обращения.",
            reply_markup=one(
                ("🧮 Рассчитать неустойку", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    payments_list = await PaymentService(db).list_case_payments(case.id)
    heading = "💳 Оплаты завершённого дела" if completed else "💳 Оплаты"
    text = heading + f"\nОбращение № {case.case_number}\n\n" + (
        "По этому делу платежей нет."
        if not payments_list
        else "\n\n".join(payment_summary_line(payment) for payment in payments_list)
    )
    if completed:
        text += (
            "\n\n✅ Дело завершено. Платежи доступны только для просмотра; "
            "новые платежи из этого архива не создаются."
        )
    items = [
        (payment_action_label(payment), f"pay_open:{payment.id}")
        for payment in payments_list
    ]
    if completed:
        items.extend(
            [
                ("🕘 История дела", "case_history_open"),
                ("📁 Итог дела", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        )
    else:
        items.extend(
            [
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        )
    await callback.message.edit_text(text, reply_markup=one(*items))


@router.callback_query(lambda c: callback_matches_action(c.data, "pay_start_30000"))
async def pay_30000(callback: CallbackQuery, db):
    await start_payment(
        callback,
        db,
        PaymentCode.M1_INITIAL_PAYMENT,
        action="pay_start_30000",
    )


@router.callback_query(lambda c: callback_matches_action(c.data, "consult_pay"))
async def consult_pay(callback: CallbackQuery, db):
    scope = await resolve_case_callback_scope(
        callback,
        db,
        action="consult_pay",
    )
    if scope is None:
        return

    if not payments_disabled():
        await start_payment(
            callback,
            db,
            PaymentCode.M2_CONSULTATION_PAYMENT,
            scope=scope,
        )
        return

    ctx, user, case = scope.ctx, scope.user, scope.case
    if not case:
        await callback.message.edit_text(
            "Активная консультация не найдена. Новая запись не создавалась.",
            reply_markup=one(
                ("👨‍⚖ Консультация", "consult_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if str(case.route or "") != RouteCode.M2.value:
        await callback.message.edit_text(
            "Эта кнопка относится к консультации, но сейчас активно другое дело. "
            "Новая запись и платёж не создавались.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value:
        await callback.message.edit_text(
            "✅ Консультация уже подтверждена. Повторное действие не требуется.",
            reply_markup=one(
                ("👨‍⚖ Открыть запись и подготовку", "consultation_booked_open"),
                ("📄 Документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if str(case.status) != CaseStatus.M2_PAYMENT_PENDING.value:
        await callback.message.edit_text(
            "Эта кнопка подтверждения времени устарела. Никаких изменений не выполнено.",
            reply_markup=one(
                ("👨‍⚖ Открыть консультацию", "consult_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        consultation = await ConsultationNoPaymentBookingService(db).confirm(
            case=case,
            client_id=user.id,
        )
        # Everything needed by Telegram after the transaction is copied before
        # commit. The presentation must not depend on a live ORM instance after
        # a transaction boundary even though the normal session uses
        # expire_on_commit=False.
        case_number = str(case.case_number)
        scheduled_at = consultation.scheduled_at
        date_text = (
            format_business_datetime(scheduled_at)
            if scheduled_at
            else "время уточняется"
        )
        await db.commit()
    except ConsultationDescriptionRequired as error:
        await db.rollback()
        await callback.message.edit_text(
            f"📝 {error}\n\nВыбранное время не подтверждено. Сначала сохраните вопрос для юриста.",
            reply_markup=one(
                ("▶️ Описать вопрос", "consult_subject_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except SlotUnavailableError as error:
        # require_payable_slot may release an expired hold and move the
        # consultation back to slot selection. Persist that cleanup instead of
        # rolling it back and trapping the client on the same dead reservation.
        await db.commit()
        await callback.message.edit_text(
            f"⏳ {error}\n\nВопрос и документы сохранены. Выберите новое свободное время.",
            reply_markup=one(
                ("📅 Выбрать дату и время", "consult_booking_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except ValueError as error:
        await db.rollback()
        await callback.message.edit_text(
            f"Запись не подтверждена: {error}\n\nОткройте актуальное состояние консультации.",
            reply_markup=one(
                ("👨‍⚖ Открыть консультацию", "consult_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось подтвердить M2 без онлайн-оплаты")
        await callback.message.edit_text(
            "Запись временно не подтверждена. Резерв и данные дела не считаются завершёнными — "
            "обновите консультацию перед повтором.",
            reply_markup=one(
                ("🔄 Открыть консультацию", "consult_start"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _present_committed_callback(
        callback,
        "✅ Консультация подтверждена без онлайн-оплаты.\n"
        f"Обращение № {case_number}\n\n"
        f"Дата и время: {date_text}\n\n"
        "Дополнительный платёж не создавался. Вопрос и документы сохранены; "
        "откройте запись, чтобы проверить подготовку и при необходимости перенести или отменить встречу.",
        reply_markup=one(
            ("👨‍⚖ Открыть запись и подготовку", "consultation_booked_open"),
            ("📄 Документы", "documents_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _show_missing_m1_payment_case(callback: CallbackQuery, db, ctx, user) -> None:
    completed = await latest_completed_strict_m1_case_for_user(db, user_id=user.id)
    if completed:
        await callback.message.edit_text(
            f"✅ Дело {completed.case_number} уже завершено.\n\n"
            "Эта старая кнопка оплаты больше не создаёт платежей. "
            "Проверьте итог или платёжную историю завершённого M1-дела.",
            reply_markup=one(
                ("💳 Оплаты", "payments_open"),
                ("📁 Итог дела", "my_case_open"),
                ("🕘 История", "case_history_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await callback.message.edit_text(
        "Активное M1-дело для этой оплаты не найдено. Новый платёж не создавался.",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def start_payment(
    callback: CallbackQuery,
    db,
    code,
    *,
    action: str | None = None,
    scope=None,
):
    if scope is None and action:
        scope = await resolve_case_callback_scope(callback, db, action=action)
        if scope is None:
            return

    if scope is None:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        case = await ctx.case_service.get_active_case_for_user(user.id)
    else:
        ctx, user, case = scope.ctx, scope.user, scope.case

    is_m1_payment = code in M1_PAYMENT_EXPECTED_STATUSES
    if not case:
        if is_m1_payment:
            await _show_missing_m1_payment_case(callback, db, ctx, user)
        else:
            await callback.answer(
                "Сначала опишите вопрос и выберите дату и время консультации.",
                show_alert=True,
            )
        return

    if is_m1_payment and not _m1_payment_context_matches(case, code):
        await callback.message.edit_text(
            "Эта кнопка оплаты относится к другому или уже завершённому этапу. "
            "Новый платёж не создавался.",
            reply_markup=one(
                ("💳 Оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if code == PaymentCode.M2_CONSULTATION_PAYMENT and str(case.route or "") != RouteCode.M2.value:
        await callback.message.edit_text(
            "Эта кнопка консультации относится к другому обращению. "
            "Платёж и новая консультация не создавались.",
            reply_markup=one(
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    # Snapshot identity before any transaction boundary. In particular the
    # provider RuntimeError path rolls the session back, which can expire ORM
    # state independently of the global expire_on_commit setting.
    case_id = int(case.id)
    case_number = str(case.case_number)
    service = PaymentService(db)
    try:
        payment = await service.get_or_create_payment(
            case=case,
            payment_code=code,
        )
        if is_m1_payment and payments_disabled():
            payment_title = str(payment.title)
            payment_amount = payment.amount
            await db.commit()
            await _present_committed_callback(
                callback,
                f"💳 {payment_title}\n"
                f"Обращение № {case_number}\n\n"
                f"Сумма: {money(payment_amount)}\n\n"
                "Онлайн-оплата сейчас отключена. Платёж уже зафиксирован в системе как ожидающий; "
                "команда изменит этап только после проверки фактического поступления денег.",
                reply_markup=one(
                    ("💳 Оплаты", "payments_open"),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return
        payment = await service.create_payment_link(payment)
        # The payment projection is now final for this transaction. Build all UI
        # data before commit so presentation cannot trigger implicit database I/O
        # after the durable financial write.
        payment_title = str(payment.title)
        payment_amount = payment.amount
        payment_markup = payment_keyboard(payment)
        await db.commit()
    except ConsultationDescriptionRequired as error:
        await db.rollback()
        await callback.message.edit_text(
            f"📝 {error}\n\nОписание и документы не изменены.",
            reply_markup=one(
                ("▶️ Описать вопрос", "consult_subject_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except SlotUnavailableError as error:
        # The service may have released an expired hold before raising. Commit
        # that cleanup so the next click can actually choose a fresh slot.
        await db.commit()
        await callback.message.edit_text(
            f"⏳ {error}\n\nВопрос и документы сохранены. Выберите новое свободное время.",
            reply_markup=one(
                ("📅 Выбрать дату и время", "consult_booking_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except ValueError as error:
        await db.rollback()
        if code == PaymentCode.M2_CONSULTATION_PAYMENT:
            await callback.message.edit_text(
                f"⏳ {error}\n\nВопрос и документы сохранены. Выберите новое свободное время.",
                reply_markup=one(
                    ("📅 Выбрать дату и время", "consult_booking_start"),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
        else:
            await callback.message.edit_text(
                f"Оплата не открыта: {error}\n\nДанные дела не изменены.",
                reply_markup=one(
                    ("💳 Оплаты", "payments_open"),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
        return
    except RuntimeError:
        await db.rollback()
        await callback.message.edit_text(
            "Платёжный сервис временно недоступен. Данные текущего этапа сохранены.",
            reply_markup=(
                one(
                    (
                        "🔄 Повторить оплату",
                        bound_case_callback("consult_pay", case_id),
                    ),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                )
                if code == PaymentCode.M2_CONSULTATION_PAYMENT
                else one(
                    ("💳 Оплаты", "payments_open"),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                )
            ),
        )
        return

    if code == PaymentCode.M2_CONSULTATION_PAYMENT:
        text = (
            f"💳 {payment_title}\n"
            f"Обращение № {case_number}\n\n"
            f"Сумма: {money(payment_amount)}\n\n"
            "Вопрос и документы сохранены. После подтверждения оплаты выбранный "
            "слот станет окончательно вашим. Не используйте эту ссылку после выбора другого времени."
        )
    elif code == PaymentCode.M1_INITIAL_PAYMENT:
        text = (
            f"💳 {payment_title}\n"
            f"Обращение № {case_number}\n\n"
            f"Сумма: {money(payment_amount)}\n\n"
            "После подтверждения оплаты система откроет следующий этап — оформление доверенности."
        )
    else:
        text = (
            f"💳 {payment_title}\n"
            f"Обращение № {case_number}\n\n"
            f"Сумма: {money(payment_amount)}\n\n"
            "После подтверждения оплаты система откроет исполнительный этап."
        )
    await _present_committed_callback(
        callback,
        text,
        reply_markup=payment_markup,
    )


@router.callback_query(lambda c: c.data.startswith("pay_open:"))
async def open_payment(callback: CallbackQuery, db):
    try:
        payment_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.message.edit_text(
            "Эта кнопка оплаты больше не актуальна.",
            reply_markup=one(
                ("💳 Открыть оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    payment, case = await get_owned_payment(callback, db, payment_id)
    if not payment or not case:
        return

    is_booked_m2 = bool(
        str(case.route or "") == RouteCode.M2.value
        and str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value
    )
    if payment.status == PaymentStatus.PAID_REVIEW:
        context_note = (
            "\n\n✅ Ваша подтверждённая консультация остаётся записанной. "
            "Сверка этого платежа не переносит и не отменяет встречу."
            if is_booked_m2
            else (
                "\n\nПока сверка не завершена, бот не меняет запись автоматически. "
                "Команда проверит платёж и сообщит следующий шаг."
            )
        )
        await callback.message.edit_text(
            f"💳 {payment.title}\n"
            f"Обращение № {case.case_number}\n"
            f"Сумма: {money(payment.amount)}\n"
            f"Статус: {payment_status_label(payment.status)}"
            "\n\n⚠️ Деньги поступили, но автоматическое применение платежа остановлено для безопасной сверки."
            f"{context_note}",
            reply_markup=payment_review_keyboard(case),
        )
        return

    if payment.status == PaymentStatus.REFUND_PENDING and is_booked_m2:
        await callback.message.edit_text(
            f"💳 {payment.title}\n"
            f"Обращение № {case.case_number}\n"
            f"Сумма: {money(payment.amount)}\n"
            f"Статус: {payment_status_label(payment.status)}\n\n"
            "Возврат этого платежа обрабатывается отдельно от записи на консультацию. "
            "✅ Текущая подтверждённая встреча остаётся без изменений.",
            reply_markup=payment_review_keyboard(case),
        )
        return

    status_note = client_payment_status_note(payment)
    await callback.message.edit_text(
        f"💳 {payment.title}\n"
        f"Обращение № {case.case_number}\n"
        f"Сумма: {money(payment.amount)}\n"
        f"Статус: {client_payment_status_label(payment)}"
        + (f"\n\n{status_note}" if status_note else ""),
        reply_markup=payment_keyboard(payment),
    )


@router.callback_query(lambda c: c.data.startswith("pay_fake_success:"))
async def fake(callback: CallbackQuery, db):
    if not fake_payments_enabled():
        await callback.answer("DEV-оплата отключена.", show_alert=True)
        return

    try:
        payment_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer("Эта кнопка оплаты больше не актуальна.", show_alert=True)
        return
    payment, case = await get_owned_payment(callback, db, payment_id)
    if not payment or not case:
        return
    if payment.provider not in {None, "fake"}:
        await callback.answer(
            "Этот платёж создан другим провайдером.",
            show_alert=True,
        )
        return

    try:
        await PaymentWebhookService(db).process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"dev": True},
        )
        # Snapshot the webhook result before commit. This deliberately makes the
        # presentation layer independent of ORM expiration and preserves correct
        # behaviour if the session policy changes later.
        payment_status = payment.status
        payment_code = payment.payment_code
        is_booked_m2 = bool(
            str(case.route or "") == RouteCode.M2.value
            and str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value
        )
        review_markup = payment_review_keyboard(case)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Не удалось подтвердить тестовую оплату")
        await callback.message.edit_text(
            "Оплата пока не подтверждена. Данные дела сохранены.",
            reply_markup=one(
                ("🔄 Проверить оплату", f"pay_open:{payment_id}"),
                ("💳 Все оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if payment_status == PaymentStatus.PAID_REVIEW:
        await _present_committed_callback(
            callback,
            (
                "⚠️ Оплата получена и передана команде на сверку. "
                "Автоматических изменений записи не выполнено."
                + (
                    "\n\n✅ Ваша подтверждённая консультация остаётся без изменений."
                    if is_booked_m2
                    else "\n\nКоманда проверит деньги и сообщит следующий безопасный шаг."
                )
            ),
            reply_markup=review_markup,
        )
        return

    if payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
        await _present_committed_callback(
            callback,
            "✅ Оплата подтверждена, консультация забронирована.\n\n"
            "Вопрос уже сохранён. Проверьте дату, документы и подготовку к встрече.",
            reply_markup=one(
                ("👨‍⚖ Открыть запись и подготовку", "consultation_booked_open"),
                ("📄 Документы", "documents_open"),
                ("✉️ Задать вопрос команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if payment_code == PaymentCode.M1_SUCCESS_FEE:
        await _present_committed_callback(
            callback,
            "✅ Финальный платёж подтверждён. Дело закрыто.\n\n"
            "Итог, документы, платежи и история остаются доступны в архиве только для просмотра.",
            reply_markup=one(
                ("📁 Итог дела", "my_case_open"),
                ("💳 Все оплаты", "payments_open"),
                ("🕘 История", "case_history_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _present_committed_callback(
        callback,
        "✅ Оплата подтверждена. Следующий этап открыт автоматически.",
        reply_markup=one(
            ("💳 Все оплаты", "payments_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
