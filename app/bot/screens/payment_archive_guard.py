from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import payments as payment_screen
from app.bot.screens.m1_stale_view_guard import router as m1_stale_view_guard_router
from app.config import settings
from app.domain.cases.client_case_scope import (
    CLIENT_COMPLETED_CASE_STATUSES,
    latest_completed_strict_m1_case_for_user,
)
from app.domain.payments.client_payment_reconciliation import (
    ACTIVE_LINK_STATUSES,
    ClientPaymentReconciliationService,
)
from app.domain.payments.mode import payments_disabled
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus

router = Router()
logger = logging.getLogger(__name__)
# payment_archive_guard is mounted before legacy m1_stages/payments in bot.py.
# Stale informational and payment callbacks live here so old Telegram controls
# are reconciled to durable case/payment state before legacy renderers run.
router.include_router(m1_stale_view_guard_router)
_COMPLETED_VALUES = {str(status) for status in CLIENT_COMPLETED_CASE_STATUSES}


def _case_is_completed(case) -> bool:
    return bool(case and str(case.status) in _COMPLETED_VALUES)


def _fake_payment_mutation_allowed() -> bool:
    """A presentation/demo flag is never authorization to mutate money."""

    return bool(
        str(settings.payment_provider or "").strip().lower() == "fake"
        and str(settings.app_env or "").strip().lower() in {"local", "test"}
    )


def _archive_keyboard():
    return one(
        ("💳 Все оплаты", "payments_open"),
        ("🕘 История дела", "case_history_open"),
        ("📁 Архив дела", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    )


def _archive_payment_text(payment) -> str:
    status = payment_screen.client_payment_status_label(payment)
    note = payment_screen.client_payment_status_note(payment)
    lines = [
        "💳 ПЛАТЁЖ ИЗ АРХИВА",
        "",
        str(payment.title or "Оплата"),
        f"Сумма: {payment_screen.money(payment.amount)}",
        f"Статус в истории: {status}",
    ]
    if note:
        lines.extend(["", note])
    lines.extend(
        [
            "",
            "✅ Дело завершено.",
            "Платёжная запись доступна только для просмотра. Старые ссылки оплаты и тестовые действия скрыты, чтобы закрытое дело нельзя было изменить из Telegram.",
        ]
    )
    return "\n".join(lines)


def _payment_id(callback: CallbackQuery) -> int | None:
    try:
        return int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        return None


def _is_active_m2_link(payment, case) -> bool:
    return bool(
        payment
        and case
        and payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT
        and payment.status in ACTIVE_LINK_STATUSES
        and str(case.route or "") == RouteCode.M2.value
    )


def _m2_stale_link_keyboard(case):
    items = []
    if str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value:
        items.append(("👨‍⚖ Текущая запись", "consultation_booked_open"))
    items.extend(
        [
            ("📁 Обновить Моё дело", "my_case_open"),
            ("💳 Все оплаты", "payments_open"),
            ("✉️ Написать команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    return one(*items)


async def _render_m2_reconciliation_failure(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "⚠️ Не удалось безопасно проверить актуальность ссылки оплаты.\n\n"
        "Переход к платёжному провайдеру скрыт: бот не будет показывать старую ссылку, пока не сверит её с текущей записью. Данные дела и деньги не изменены.\n\n"
        "Обновите «Моё дело» или напишите команде.",
        reply_markup=one(
            ("📁 Обновить Моё дело", "my_case_open"),
            ("✉️ Написать команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _reconcile_m2_payment_view(callback: CallbackQuery, db, payment, case):
    if not _is_active_m2_link(payment, case):
        return payment, case, False
    try:
        payment, case, changed = await ClientPaymentReconciliationService(db).reconcile(
            payment_id=int(payment.id),
            case_id=int(case.id),
        )
        await db.commit()
        return payment, case, changed
    except (LookupError, ValueError) as error:
        await db.rollback()
        logger.warning(
            "M2 payment view reconciliation rejected payment_id=%s case_id=%s: %s",
            getattr(payment, "id", None),
            getattr(case, "id", None),
            error,
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "M2 payment view reconciliation failed payment_id=%s case_id=%s",
            getattr(payment, "id", None),
            getattr(case, "id", None),
        )
    await _render_m2_reconciliation_failure(callback)
    return None, None, False


@router.callback_query(lambda c: c.data == "payments_open")
async def guard_active_m2_payment_list(callback: CallbackQuery, db):
    """Synchronize active M2 links before the client sees the payment list."""

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case is None or str(case.route or "") != RouteCode.M2.value:
        await payment_screen.payments(callback, db)
        return

    payments = await PaymentService(db).list_case_payments(case.id)
    active_links = [
        payment
        for payment in payments
        if payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT
        and payment.status in ACTIVE_LINK_STATUSES
    ]
    try:
        service = ClientPaymentReconciliationService(db)
        for payment in active_links:
            await service.reconcile(
                payment_id=int(payment.id),
                case_id=int(case.id),
            )
        if active_links:
            await db.commit()
    except (LookupError, ValueError) as error:
        await db.rollback()
        logger.warning(
            "M2 payment list reconciliation rejected case_id=%s: %s",
            case.id,
            error,
        )
        await _render_m2_reconciliation_failure(callback)
        return
    except Exception:
        await db.rollback()
        logger.exception("M2 payment list reconciliation failed case_id=%s", case.id)
        await _render_m2_reconciliation_failure(callback)
        return

    await payment_screen.payments(callback, db)


@router.callback_query(lambda c: bool(c.data) and c.data.startswith("pay_open:"))
async def guard_archived_payment_open(callback: CallbackQuery, db):
    """Keep completed payments read-only and active M2 links reservation-bound."""

    payment_id = _payment_id(callback)
    if payment_id is None:
        await payment_screen.open_payment(callback, db)
        return

    payment, case = await payment_screen.get_owned_payment(callback, db, payment_id)
    if not payment or not case:
        return
    if _case_is_completed(case):
        await callback.message.edit_text(
            _archive_payment_text(payment),
            reply_markup=_archive_keyboard(),
        )
        return

    payment, case, _changed = await _reconcile_m2_payment_view(
        callback,
        db,
        payment,
        case,
    )
    if payment is None or case is None:
        return
    if (
        payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT
        and payment.status == PaymentStatus.EXPIRED
    ):
        if str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value:
            text = (
                "⏳ Эта старая ссылка оплаты больше не действует.\n\n"
                "✅ Текущая подтверждённая консультация сохранена. Старый платёж не меняет её дату, слот или статус."
            )
        else:
            text = (
                "⏳ Эта ссылка оплаты больше не соответствует текущему резерву консультации.\n\n"
                "Переход к провайдеру скрыт. Вопрос и документы сохранены; откройте актуальное дело и продолжите с текущего шага."
            )
        await callback.message.edit_text(
            text,
            reply_markup=_m2_stale_link_keyboard(case),
        )
        return

    await payment_screen.open_payment(callback, db)


@router.callback_query(lambda c: bool(c.data) and c.data.startswith("pay_fake_success:"))
async def guard_archived_fake_success(callback: CallbackQuery, db):
    """Keep every fake-payment mutation local/test-only, including old buttons."""

    payment_id = _payment_id(callback)
    if not _fake_payment_mutation_allowed():
        await callback.message.edit_text(
            "Эта тестовая кнопка оплаты недоступна в текущем окружении. Финансовый статус не изменён.\n\n"
            "Используйте реальную ссылку оплаты либо дождитесь подтверждения команды.",
            reply_markup=one(
                ("💳 Все оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if payment_id is None:
        await callback.message.edit_text(
            "Тестовая кнопка повреждена. Платёж не изменён.",
            reply_markup=one(
                ("💳 Все оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    payment, case = await payment_screen.get_owned_payment(callback, db, payment_id)
    if not payment or not case:
        return
    if not _case_is_completed(case):
        payment, case, _changed = await _reconcile_m2_payment_view(
            callback,
            db,
            payment,
            case,
        )
        if payment is None or case is None:
            return
        if (
            payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT
            and payment.status == PaymentStatus.EXPIRED
        ):
            await callback.message.edit_text(
                "⏳ Тестовая кнопка относится к устаревшему резерву. Платёж не подтверждён и запись не изменена.",
                reply_markup=_m2_stale_link_keyboard(case),
            )
            return
        await payment_screen.fake(callback, db)
        return

    await callback.message.edit_text(
        _archive_payment_text(payment)
        + "\n\nСтарая кнопка подтверждения оплаты больше не действует.",
        reply_markup=_archive_keyboard(),
    )


@router.callback_query(lambda c: c.data == "pay_success_fee")
async def guard_success_fee_stage(callback: CallbackQuery, db):
    """Let the client pay an existing success fee but never create its business stage.

    The lawyer enforcement service records the recovered amount, creates the
    success-fee obligation and moves the case to M1_WAITING_SUCCESS_FEE. A stale
    Telegram callback must not perform that transition on behalf of the client.
    """

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case is None:
        completed = await latest_completed_strict_m1_case_for_user(db, user_id=user.id)
        if completed:
            await callback.message.edit_text(
                f"✅ Дело {completed.case_number} уже завершено.\n\n"
                "Старая кнопка финального платежа больше не выполняет действий.",
                reply_markup=_archive_keyboard(),
            )
        else:
            await callback.message.edit_text(
                "Активное M1-дело не найдено. Финальный платёж не создавался.",
                reply_markup=one(
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
        return

    status = str(case.status)
    if status == CaseStatus.M1_MONEY_RECEIVED.value:
        await callback.message.edit_text(
            "Фактическое взыскание уже отмечено, но финальный платёж ещё не открыт ответственным юристом.\n\n"
            "Нажатие старой кнопки не меняет этап дела и не создаёт обязательство от вашего имени.",
            reply_markup=one(
                ("🔄 Обновить Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("💳 Оплаты", "payments_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if status != CaseStatus.M1_WAITING_SUCCESS_FEE.value:
        await callback.message.edit_text(
            "Эта кнопка финального платежа больше не соответствует текущему этапу. Новый платёж не создавался.",
            reply_markup=one(
                ("💳 Оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    service = PaymentService(db)
    payments = await service.list_case_payments(case.id)
    payment = next(
        (
            item
            for item in reversed(payments)
            if str(item.payment_code) == PaymentCode.M1_SUCCESS_FEE.value
        ),
        None,
    )
    if payment is None:
        await callback.message.edit_text(
            "⚠️ Этап финального платежа открыт, но платёжная запись не найдена.\n\n"
            "Новый платёж автоматически не создавался. Команда должна проверить целостность дела.",
            reply_markup=one(
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if str(payment.status) not in {
        PaymentStatus.PENDING.value,
        PaymentStatus.WAITING_CONFIRMATION.value,
    }:
        await payment_screen.open_payment(callback, db)
        return

    if payments_disabled():
        await callback.message.edit_text(
            "💳 Финальный платёж\n\n"
            f"Сумма: {payment_screen.money(payment.amount)}\n"
            f"Статус: {payment_screen.client_payment_status_label(payment)}\n\n"
            "Онлайн-оплата отключена. Команда изменит этап только после проверки фактического поступления.",
            reply_markup=one(
                ("💳 Все оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if not payment.payment_url:
        try:
            payment = await service.create_payment_link(payment)
            await db.commit()
        except (RuntimeError, ValueError) as error:
            await db.rollback()
            await callback.message.edit_text(
                f"Ссылка оплаты пока не открыта: {error}\n\n"
                "Сам платёж и этап дела сохранены. Повторите через «Оплаты» или напишите команде.",
                reply_markup=one(
                    ("💳 Все оплаты", "payments_open"),
                    ("✉️ Написать команде", "message_create"),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return

    await callback.message.edit_text(
        "💳 Финальный платёж\n\n"
        f"Сумма: {payment_screen.money(payment.amount)}\n\n"
        "Платёж уже сформирован после подтверждённого факта взыскания. После подтверждения оплаты дело будет закрыто.",
        reply_markup=payment_screen.payment_keyboard(payment),
    )
