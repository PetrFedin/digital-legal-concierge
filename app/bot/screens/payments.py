import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.consultations.payment_lifecycle_service import (
    ConsultationPaymentLifecycleError,
    ConsultationPaymentLifecycleService,
)
from app.domain.payments.m1_payment_lifecycle_service import (
    M1PaymentLifecycleError,
    M1PaymentLifecycleService,
)
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
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

PAYMENT_STATUS_DESCRIPTIONS = {
    PaymentStatus.PENDING.value: "Счёт выставлен, но подтверждение оплаты ещё не получено.",
    PaymentStatus.WAITING_CONFIRMATION.value: (
        "Платёж поступил и проходит техническую или ручную проверку."
    ),
    PaymentStatus.PAID.value: (
        "Оплата подтверждена. Связанный этап дела уже открыт или будет "
        "обновлён автоматически."
    ),
    PaymentStatus.FAILED.value: (
        "Провайдер не подтвердил оплату. Можно повторить попытку или "
        "обратиться к менеджеру."
    ),
    PaymentStatus.CANCELLED.value: (
        "Платёж отменён. Новая ссылка будет создана только на актуальном "
        "этапе дела."
    ),
    PaymentStatus.REFUNDED.value: (
        "Средства возвращены плательщику. Подробности можно уточнить у менеджера."
    ),
    PaymentStatus.EXPIRED.value: (
        "Срок действия ссылки закончился. Откройте текущий шаг дела для "
        "новой попытки."
    ),
}

PAYMENT_PURPOSES = {
    PaymentCode.M1_INITIAL_PAYMENT.value: (
        "Первый этап полного ведения дела",
        "После подтверждения оплаты откроется оформление доверенности и "
        "претензионная работа.",
    ),
    PaymentCode.M1_COURT_PAYMENT.value: (
        "Судебный этап полного ведения дела",
        "После подтверждения оплаты юрист сможет продолжить судебную работу "
        "и дальнейшее исполнение решения.",
    ),
    PaymentCode.M1_SUCCESS_FEE.value: (
        "Итоговое вознаграждение по результату",
        "После подтверждения оплаты финансовые обязательства будут закрыты, "
        "а дело перейдёт к завершению.",
    ),
    PaymentCode.M2_CONSULTATION_PAYMENT.value: (
        "Юридическая консультация",
        "После оплаты выбранное время закрепляется за вами и ожидает "
        "подтверждения назначенного юриста.",
    ),
}


def _value(value) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


def money(value):
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def _status_title(payment) -> str:
    if getattr(payment, "manual_review_required", False):
        return "Требуется проверка сотрудником"
    return PAYMENT_STATUS_TITLES.get(_value(payment.status), "Статус уточняется")


def _status_description(payment) -> str:
    if getattr(payment, "manual_review_required", False):
        return (
            "Платёж сохранён, но автоматическое продолжение остановлено для "
            "безопасной проверки. Повторно платить не нужно, пока сотрудник "
            "не уточнит статус."
        )
    return PAYMENT_STATUS_DESCRIPTIONS.get(
        _value(payment.status),
        "Мы уточняем состояние платежа. При необходимости сотрудник свяжется с вами.",
    )


def _payment_purpose(payment_code) -> tuple[str, str]:
    return PAYMENT_PURPOSES.get(
        _value(payment_code),
        (
            "Оплата по делу",
            "После подтверждения платежа статус дела будет обновлён.",
        ),
    )


def _payment_card(payment, *, compact: bool = False) -> str:
    purpose, next_step = _payment_purpose(payment.payment_code)
    if compact:
        return f"• {purpose}\n  {money(payment.amount)} · {_status_title(payment)}"
    return (
        "💳 Платёж\n"
        "━━━━━━━━━━━━━━━━\n"
        f"Назначение: {purpose}\n"
        f"Сумма: {money(payment.amount)}\n"
        f"Статус: {_status_title(payment)}\n\n"
        f"🔎 Что это означает\n{_status_description(payment)}\n\n"
        f"➡️ Что будет дальше\n{next_step}\n\n"
        "Важно: не создавайте повторную оплату, если деньги уже списаны. "
        "При спорном статусе используйте связь с менеджером."
    )


def _payment_buttons(payment):
    items = []
    status = _value(payment.status)
    payable = status in {
        PaymentStatus.PENDING.value,
        PaymentStatus.WAITING_CONFIRMATION.value,
    }
    if payable and payment.payment_url and not payment.manual_review_required:
        items.append(("💳 Перейти к безопасной оплате", payment.payment_url))
    if payment.manual_review_required:
        items.append(("💬 Уточнить у менеджера", "contact_lawyer"))
    items.extend(
        [
            ("💳 Все оплаты", "payments_open"),
            ("📁 Моё дело", "my_case_open"),
        ]
    )
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
        "⚠️ Не удалось продолжить оплату\n\n"
        f"{text}\n\n"
        "Откройте карточку дела: там всегда указано актуальное действие. "
        "Если деньги уже списаны, не оплачивайте повторно.",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("💬 Связаться с менеджером", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "payments_open")
async def payments(callback: CallbackQuery, db):
    _, case = await _load_owned_case(callback, db)
    if case is None:
        await _show_payment_error(
            callback,
            "Активное дело не найдено. Платежи доступны только внутри вашего "
            "текущего дела.",
        )
        return

    payments_list = await PaymentService(db).list_case_payments(case.id)
    if not payments_list:
        text = (
            "💳 Оплаты по делу\n\n"
            "Сейчас нет выставленных платежей.\n\n"
            "Счёт появляется только на соответствующем этапе маршрута. "
            "Бот уведомит вас, когда потребуется оплата."
        )
        items = [("📁 Моё дело", "my_case_open"), ("🏠 Главная", "nav_home")]
    else:
        text = (
            f"💳 Оплаты по делу № {case.case_number}\n"
            "━━━━━━━━━━━━━━━━\n"
            + "\n\n".join(
                _payment_card(payment, compact=True) for payment in payments_list
            )
            + "\n\nОткройте нужный платёж, чтобы увидеть назначение, статус "
            "и дальнейшие действия."
        )
        items = [
            (
                f"{'✅' if _value(payment.status) == PaymentStatus.PAID.value else '💳'} "
                f"{_payment_purpose(payment.payment_code)[0][:35]}",
                f"pay_open:{payment.id}",
            )
            for payment in payments_list
        ]
        items.append(("📁 Моё дело", "my_case_open"))
    await callback.message.edit_text(text, reply_markup=one(*items))


@router.callback_query(lambda c: c.data == "pay_start_30000")
async def pay_30000(callback: CallbackQuery, db):
    await start_payment(callback, db, PaymentCode.M1_INITIAL_PAYMENT)


@router.callback_query(lambda c: c.data == "pay_court_70000")
async def pay_court(callback: CallbackQuery, db):
    await start_payment(callback, db, PaymentCode.M1_COURT_PAYMENT)


@router.callback_query(lambda c: c.data == "pay_success_fee")
async def pay_success_fee(callback: CallbackQuery, db):
    await start_payment(callback, db, PaymentCode.M1_SUCCESS_FEE)


@router.callback_query(lambda c: c.data == "consult_pay")
async def consult_pay(callback: CallbackQuery, db):
    user, case = await _load_owned_case(callback, db)
    if case is None:
        await _show_payment_error(
            callback,
            "Активное дело не найдено. Откройте «Моё дело» и продолжите с "
            "текущего шага.",
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
            "Не удалось подготовить оплату. Проверьте выбранное время и "
            "попробуйте ещё раз.",
        )
        return

    try:
        await callback.message.edit_text(
            _payment_card(payment),
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
        await _show_payment_error(
            callback,
            "Активное дело не найдено. Откройте карточку дела и продолжите с "
            "актуального этапа.",
        )
        return

    try:
        M1PaymentLifecycleService.validate_payment_request(
            case=case,
            payment_code=code,
        )
        service = PaymentService(db)
        payment = await service.get_or_create_payment(
            case=case,
            payment_code=code,
        )
        payment = await service.create_payment_link(payment)
        await db.commit()
    except M1PaymentLifecycleError as exc:
        await db.rollback()
        await _show_payment_error(callback, str(exc))
        return
    except Exception:
        await db.rollback()
        logger.exception(
            "Unexpected error while preparing Telegram M1 payment",
            extra={"case_id": case.id, "payment_code": _value(code)},
        )
        await _show_payment_error(
            callback,
            "Не удалось сформировать ссылку. Попробуйте немного позже или "
            "обратитесь к менеджеру.",
        )
        return

    await callback.message.edit_text(
        _payment_card(payment),
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
        _payment_card(payment),
        reply_markup=_payment_buttons(payment),
    )


@router.callback_query(lambda c: c.data.startswith("pay_fake_success:"))
async def fake(callback: CallbackQuery, db):
    from app.config import settings

    if settings.app_env != "local":
        await callback.answer("Тестовое подтверждение отключено.", show_alert=True)
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
            "✅ Оплата получена\n\n"
            "Выбранное время закреплено за вами. Теперь назначенный юрист "
            "должен подтвердить консультацию. Повторная оплата не требуется.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("💳 Все оплаты", "payments_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    _, next_step = _payment_purpose(payment.payment_code)
    await callback.message.edit_text(
        "✅ Оплата подтверждена\n\n"
        f"{next_step}\n\n"
        "Откройте карточку дела, чтобы увидеть обновлённый статус и следующий шаг.",
        reply_markup=one(
            ("📁 Открыть моё дело", "my_case_open"),
            ("💳 Все оплаты", "payments_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
