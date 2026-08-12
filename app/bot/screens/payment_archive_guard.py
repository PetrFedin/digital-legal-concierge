from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.keyboards import one
from app.bot.screens import payments as payment_screen
from app.domain.cases.client_case_scope import CLIENT_COMPLETED_CASE_STATUSES

router = Router()
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


async def _payment_context(callback: CallbackQuery, db):
    try:
        payment_id = int((callback.data or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        return None, None
    return await payment_screen.get_owned_payment(callback, db, payment_id)


@router.callback_query(lambda c: bool(c.data) and c.data.startswith("pay_open:"))
async def guard_archived_payment_open(callback: CallbackQuery, db):
    """Keep completed-case payment details strictly read-only.

    Old Telegram messages can survive much longer than a case. If a pending
    payment link was rendered before closure, opening that record later must not
    surface the URL or a DEV-success action again.
    """

    payment, case = await _payment_context(callback, db)
    if not payment or not case:
        await payment_screen.open_payment(callback, db)
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

    payment, case = await _payment_context(callback, db)
    if not payment or not case:
        await payment_screen.fake(callback, db)
        return
    if not _case_is_completed(case):
        await payment_screen.fake(callback, db)
        return

    await callback.message.edit_text(
        _archive_payment_text(payment)
        + "\n\nСтарая кнопка подтверждения оплаты больше не действует.",
        reply_markup=_archive_keyboard(),
    )
