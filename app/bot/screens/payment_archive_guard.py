from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import payments as payment_screen
from app.bot.screens.m1_stale_view_guard import router as m1_stale_view_guard_router
from app.domain.cases.client_case_scope import (
    CLIENT_COMPLETED_CASE_STATUSES,
    latest_completed_strict_m1_case_for_user,
)
from app.domain.payments.mode import payments_disabled
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus

router = Router()
# payment_archive_guard is mounted before legacy m1_stages in bot.py. Stale
# informational callbacks live here so old POA/court buttons are reconciled to
# the actual case state before the historical view handlers can render them.
router.include_router(m1_stale_view_guard_router)
_COMPLETED_VALUES = {str(status) for status in CLIENT_COMPLETED_CASE_STATUSES}


def _case_is_completed(case) -> bool:
    return bool(case and str(case.status) in _COMPLETED_VALUES)


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


@router.callback_query(lambda c: bool(c.data) and c.data.startswith("pay_open:"))
async def guard_archived_payment_open(callback: CallbackQuery, db):
    """Keep completed-case payment details strictly read-only."""

    payment_id = _payment_id(callback)
    if payment_id is None:
        await payment_screen.open_payment(callback, db)
        return

    payment, case = await payment_screen.get_owned_payment(callback, db, payment_id)
    if not payment or not case:
        return
    if not _case_is_completed(case):
        await payment_screen.open_payment(callback, db)
        return

    await callback.message.edit_text(
        _archive_payment_text(payment),
        reply_markup=_archive_keyboard(),
    )


@router.callback_query(lambda c: bool(c.data) and c.data.startswith("pay_fake_success:"))
async def guard_archived_fake_success(callback: CallbackQuery, db):
    """Block stale DEV-success callbacks after a case has been completed."""

    payment_id = _payment_id(callback)
    if payment_id is None:
        await payment_screen.fake(callback, db)
        return

    payment, case = await payment_screen.get_owned_payment(callback, db, payment_id)
    if not payment or not case:
        return
    if not _case_is_completed(case):
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
