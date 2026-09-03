from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import bound_case_callback, resolve_case_callback_scope
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import payments as payment_screen
from app.bot.screens.m1_stale_view_guard import router as m1_stale_view_guard_router
from app.config import settings
from app.domain.cases.client_case_scope import (
    CLIENT_COMPLETED_CASE_STATUSES,
    latest_completed_strict_m1_case_for_user,
)
from app.domain.consultations.consultation_intake import ConsultationDescriptionRequired
from app.domain.consultations.slot_service import SlotUnavailableError
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
_M1_CURRENT_STAGE_BY_CODE = {
    PaymentCode.M1_INITIAL_PAYMENT.value: CaseStatus.M1_WAITING_PAYMENT_30000,
    PaymentCode.M1_COURT_PAYMENT.value: CaseStatus.M1_WAITING_PAYMENT_70000,
    PaymentCode.M1_SUCCESS_FEE.value: CaseStatus.M1_WAITING_SUCCESS_FEE,
}


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
        and str(payment.payment_code) == PaymentCode.M2_CONSULTATION_PAYMENT.value
        and str(payment.status) in {str(item) for item in ACTIVE_LINK_STATUSES}
        and str(case.route or "").upper() == RouteCode.M2.value
    )


def _m1_payment_matches_current_stage(payment, case) -> bool:
    expected = _M1_CURRENT_STAGE_BY_CODE.get(str(payment.payment_code))
    return bool(
        expected is not None
        and str(case.route or "").upper() == RouteCode.M1.value
        and str(case.status) == expected.value
    )


def _m2_stale_link_keyboard(case):
    items = []
    case_id = int(case.id)
    if str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value:
        items.append(("👨‍⚖ Текущая запись", "consultation_booked_open"))
    elif str(case.status) in {
        CaseStatus.M2_SLOT_PENDING.value,
        CaseStatus.M2_DOCUMENTS_OPTIONAL.value,
        CaseStatus.M2_DESCRIPTION_PENDING.value,
    }:
        items.append(
            (
                "📅 Выбрать актуальное время",
                bound_case_callback("consult_booking_start", case_id),
            )
        )
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
    payment_id = int(payment.id)
    case_id = int(case.id)
    try:
        _payment, _case, changed = await ClientPaymentReconciliationService(db).reconcile(
            payment_id=payment_id,
            case_id=case_id,
        )
        await db.commit()

        # Reconciliation may change the Payment, Case, Consultation and Slot in
        # one transaction. Never return the instances that crossed commit(): an
        # expire-on-commit session would make the caller's presentation access
        # perform implicit async I/O. Re-resolve the exact owned records in a new
        # transaction instead.
        fresh_payment, fresh_case = await payment_screen.get_owned_payment(
            callback,
            db,
            payment_id,
        )
        if fresh_payment is None or fresh_case is None:
            return None, None, changed
        return fresh_payment, fresh_case, changed
    except (LookupError, ValueError) as error:
        await db.rollback()
        logger.warning(
            "M2 payment view reconciliation rejected payment_id=%s case_id=%s: %s",
            payment_id,
            case_id,
            error,
        )
    except Exception:
        await db.rollback()
        logger.exception(
            "M2 payment view reconciliation failed payment_id=%s case_id=%s",
            payment_id,
            case_id,
        )
    await _render_m2_reconciliation_failure(callback)
    return None, None, False


async def _render_hold_lost(
    callback: CallbackQuery,
    error: Exception,
    *,
    case_id: int,
    case_number: str,
) -> None:
    await callback.message.edit_text(
        f"⏳ {error}\n\n"
        f"Обращение № {case_number}\n\n"
        "Старая ссылка не открыта. Вопрос и документы сохранены — выберите новое свободное время.",
        reply_markup=one(
            (
                "📅 Выбрать дату и время",
                bound_case_callback("consult_booking_start", case_id),
            ),
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Написать команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _require_selected_active_payment_case(callback: CallbackQuery, db, case) -> bool:
    """Prevent an old exact payment button from acting on a non-selected live Case.

    Archived/completed payment views are read-only and may be opened directly.
    Any action that can reconcile a live payment, create a provider URL or run a
    fake local confirmation must still match the client's current selected Case.
    """

    if _case_is_completed(case):
        return True
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    selected = await ctx.case_service.get_active_case_for_user(int(user.id))
    if selected is not None and int(selected.id) == int(case.id):
        return True

    case_number = str(case.case_number)
    await db.rollback()
    await callback.message.edit_text(
        "Эта платёжная кнопка относится к другому активному обращению. "
        "Ссылка не открыта, платёж не сверялся и финансовый статус не изменён.\n\n"
        f"Обращение из старого сообщения: № {case_number}\n\n"
        "Сначала выберите нужное обращение и откройте его актуальный раздел «Оплаты».",
        reply_markup=one(
            ("📁 Выбрать обращение", "my_cases_open"),
            ("💳 Оплаты выбранного дела", "payments_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
    return False


@router.callback_query(lambda c: c.data == "payments_open")
async def guard_active_m2_payment_list(callback: CallbackQuery, db):
    """Reconcile and materialize the exact current M2 payment before list render.

    A client must be able to leave the reservation screen and later resume from
    My Case / Payments without depending on a historical raw `consult_pay`
    callback. Creating the internal obligation is safe here only after the exact
    live consultation hold has been validated by PaymentService.
    """

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case is None or str(case.route or "").upper() != RouteCode.M2.value:
        await payment_screen.payments(callback, db)
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    service = PaymentService(db)
    payments = await service.list_case_payments(case_id)
    active_links = [
        payment
        for payment in payments
        if str(payment.payment_code) == PaymentCode.M2_CONSULTATION_PAYMENT.value
        and str(payment.status) in {str(item) for item in ACTIVE_LINK_STATUSES}
    ]
    try:
        reconciler = ClientPaymentReconciliationService(db)
        for payment in active_links:
            await reconciler.reconcile(
                payment_id=int(payment.id),
                case_id=case_id,
            )

        # Re-read status through the same non-expiring session. If a valid hold
        # is still awaiting payment, ensure its exact reservation has a Payment
        # row even when the user left the original Telegram screen before
        # pressing the old raw consult_pay button.
        if (
            not payments_disabled()
            and str(case.status) == CaseStatus.M2_PAYMENT_PENDING.value
        ):
            await service.get_or_create_payment(
                case=case,
                payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            )
        if active_links or str(case.status) == CaseStatus.M2_PAYMENT_PENDING.value:
            await db.commit()
    except SlotUnavailableError as error:
        # PaymentService intentionally restores SLOT_PENDING before raising.
        # Persist that cleanup instead of rolling the client back into the same
        # dead hold.
        await db.commit()
        await _render_hold_lost(
            callback,
            error,
            case_id=case_id,
            case_number=case_number,
        )
        return
    except ConsultationDescriptionRequired as error:
        await db.rollback()
        await callback.message.edit_text(
            f"📝 {error}\n\nОплата не открыта. Сначала сохраните вопрос для юриста.",
            reply_markup=one(
                ("📝 Описать вопрос", "consult_subject_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except (LookupError, ValueError) as error:
        await db.rollback()
        logger.warning(
            "M2 payment list reconciliation rejected case_id=%s: %s",
            case_id,
            error,
        )
        await _render_m2_reconciliation_failure(callback)
        return
    except Exception:
        await db.rollback()
        logger.exception("M2 payment list reconciliation failed case_id=%s", case_id)
        await _render_m2_reconciliation_failure(callback)
        return

    await payment_screen.payments(callback, db)


@router.callback_query(lambda c: c.data == "consult_pay")
async def legacy_consult_pay_is_navigation(callback: CallbackQuery, db):
    """Historical unbound pay buttons never select whichever Case is current now."""

    scope = await resolve_case_callback_scope(
        callback,
        db,
        action="consult_pay",
        allow_legacy_message_case_context=True,
    )
    if scope is None:
        return

    if payments_disabled():
        # In no-payment mode this callback is not financial: the canonical
        # handler confirms the exact current held slot under domain checks.
        await payment_screen.consult_pay(callback, db)
        return

    case = scope.case
    if case is not None and str(case.route or "").upper() != RouteCode.M2.value:
        await db.rollback()
        await callback.message.edit_text(
            "Эта старая кнопка относится к консультации, но сейчас активно другое дело. Платёж и новая запись не создавались.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("💳 Актуальные оплаты", "payments_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    # Use the current Payments entry point only after the legacy button has been
    # proven to belong to the selected Case. The Payments entry reconciles the
    # reservation and creates only the internal exact obligation; the provider
    # link is opened later by a concrete pay_open:<payment_id> callback.
    await guard_active_m2_payment_list(callback, db)


@router.callback_query(lambda c: bool(c.data) and c.data.startswith("pay_open:"))
async def guard_archived_payment_open(callback: CallbackQuery, db):
    """Open a provider URL only for an exact payment that still matches the case."""

    payment_id = _payment_id(callback)
    if payment_id is None:
        await payment_screen.open_payment(callback, db)
        return

    payment, case = await payment_screen.get_owned_payment(callback, db, payment_id)
    if not payment or not case:
        return
    if not await _require_selected_active_payment_case(callback, db, case):
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
        str(payment.payment_code) == PaymentCode.M2_CONSULTATION_PAYMENT.value
        and str(payment.status) == PaymentStatus.EXPIRED.value
    ):
        case_number = str(case.case_number)
        if str(case.status) == CaseStatus.M2_CONSULTATION_BOOKED.value:
            text = (
                "⏳ Эта старая ссылка оплаты больше не действует.\n\n"
                f"Обращение № {case_number}\n\n"
                "✅ Текущая подтверждённая консультация сохранена. Старый платёж не меняет её дату, слот или статус."
            )
        else:
            text = (
                "⏳ Эта ссылка оплаты больше не соответствует текущему резерву консультации.\n\n"
                f"Обращение № {case_number}\n\n"
                "Переход к провайдеру скрыт. Вопрос и документы сохранены; откройте актуальное дело и продолжите с текущего шага."
            )
        await callback.message.edit_text(
            text,
            reply_markup=_m2_stale_link_keyboard(case),
        )
        return

    payment_status = str(payment.status)
    active_payment = payment_status in {
        PaymentStatus.PENDING.value,
        PaymentStatus.WAITING_CONFIRMATION.value,
    }
    is_m2 = str(payment.payment_code) == PaymentCode.M2_CONSULTATION_PAYMENT.value
    exact_context = is_m2 or _m1_payment_matches_current_stage(payment, case)

    if active_payment and not exact_context:
        await db.rollback()
        await callback.message.edit_text(
            "ℹ️ Эта платёжная запись относится к предыдущему этапу. Сохранённая ссылка скрыта и не будет открыта из старого сообщения.\n\n"
            "Финансовый статус не изменён. Откройте актуальные оплаты текущего дела.",
            reply_markup=one(
                ("💳 Актуальные оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if active_payment and payments_disabled():
        # Presentation helpers read Payment ORM fields. Materialize the complete
        # client text before rollback releases/invalidates the current identity map.
        disabled_text = (
            f"💳 {payment.title}\n\n"
            f"Сумма: {payment_screen.money(payment.amount)}\n"
            f"Статус: {payment_screen.client_payment_status_label(payment)}\n\n"
            "Онлайн-оплата сейчас отключена. Ссылка провайдера не создаётся; команда изменит этап только после проверки фактического поступления."
        )
        await db.rollback()
        await callback.message.edit_text(
            disabled_text,
            reply_markup=one(
                ("💳 Все оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if active_payment and exact_context and not payment.payment_url:
        payment_row_id = int(payment.id)
        case_row_id = int(case.id)
        try:
            payment = await PaymentService(db).create_payment_link(payment)
            await db.commit()
        except (RuntimeError, ValueError) as error:
            await db.rollback()
            await callback.message.edit_text(
                f"Ссылка оплаты пока не открыта: {error}\n\n"
                "Платёжное обязательство и юридический этап сохранены. Повторите через «Оплаты» или напишите команде.",
                reply_markup=one(
                    ("💳 Все оплаты", "payments_open"),
                    ("✉️ Написать команде", "message_create"),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return
        except Exception:
            await db.rollback()
            logger.exception(
                "Exact client payment link creation failed payment_id=%s case_id=%s",
                payment_row_id,
                case_row_id,
            )
            await callback.message.edit_text(
                "Платёжный сервис временно недоступен. Старая ссылка не использована, данные дела сохранены.",
                reply_markup=one(
                    ("💳 Все оплаты", "payments_open"),
                    ("✉️ Написать команде", "message_create"),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
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
    if not await _require_selected_active_payment_case(callback, db, case):
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
            str(payment.payment_code) == PaymentCode.M2_CONSULTATION_PAYMENT.value
            and str(payment.status) == PaymentStatus.EXPIRED.value
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
            payment_amount = payment.amount
            payment_markup = payment_screen.payment_keyboard(payment)
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
    else:
        payment_amount = payment.amount
        payment_markup = payment_screen.payment_keyboard(payment)

    await callback.message.edit_text(
        "💳 Финальный платёж\n\n"
        f"Сумма: {payment_screen.money(payment_amount)}\n\n"
        "Платёж уже сформирован после подтверждённого факта взыскания. После подтверждения оплаты дело будет закрыто.",
        reply_markup=payment_markup,
    )
