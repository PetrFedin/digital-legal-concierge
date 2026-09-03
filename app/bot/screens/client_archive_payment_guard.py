from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.keyboards import one
from app.bot.screens import payment_archive_guard, payments
from app.domain.cases.client_case_scope import CLIENT_COMPLETED_CASE_STATUSES

router = Router()

_COMPLETED_VALUES = {str(status) for status in CLIENT_COMPLETED_CASE_STATUSES}


def _payment_id(value: str | None) -> int | None:
    try:
        payment_id = int(str(value or "").split(":", 1)[1])
    except (IndexError, TypeError, ValueError):
        return None
    return payment_id if payment_id > 0 else None


def _exact_archive_keyboard(case_id: int):
    return one(
        ("💳 Оплаты этого обращения", f"client_archive_payments:v2:{case_id}:0"),
        ("🕘 История этого обращения", f"case_history_open:v2:{case_id}"),
        ("🗄 Архив обращения", f"my_case_archive:v2:{case_id}"),
        ("📁 Активное дело", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    )


def _archive_payment_text(payment, *, case_number: str) -> str:
    status = payments.client_payment_status_label(payment)
    note = payments.client_payment_status_note(payment)
    lines = [
        "💳 ПЛАТЁЖ · АРХИВ",
        f"Обращение № {case_number}",
        "",
        "СЕЙЧАС",
        "Платёжная запись относится к завершённому обращению и доступна только для просмотра.",
        "",
        str(payment.title or "Оплата"),
        f"Сумма: {payments.money(payment.amount)}",
        f"Статус в истории: {status}",
    ]
    if note:
        lines.extend(["", note])
    lines.extend(
        [
            "",
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ",
            "Если нужно сверить контекст, вернитесь в оплаты, историю или карточку именно этого архива. Старые ссылки провайдера и тестовые подтверждения здесь не показываются.",
        ]
    )
    return "\n".join(lines)


async def _render_if_completed(callback: CallbackQuery, db) -> bool:
    """Return True when this early guard has fully handled the callback.

    A syntactically broken id is delegated to the canonical guard so it keeps
    the existing validation wording. A valid id that is missing/foreign is
    already answered by get_owned_payment and must not be processed twice.
    """

    payment_id = _payment_id(callback.data)
    if payment_id is None:
        return False
    payment, case = await payments.get_owned_payment(callback, db, payment_id)
    if payment is None or case is None:
        return True
    if str(case.status) not in _COMPLETED_VALUES:
        return False

    # Snapshot every presentation scalar before network I/O. No reconciliation,
    # provider-link creation or fake-payment transition is called for terminal Case.
    case_id = int(case.id)
    case_number = str(case.case_number)
    text = _archive_payment_text(payment, case_number=case_number)
    await db.rollback()
    await callback.message.edit_text(
        text,
        reply_markup=_exact_archive_keyboard(case_id),
    )
    return True


@router.callback_query(lambda c: str(c.data or "").startswith("pay_open:"))
async def exact_archive_payment_open(callback: CallbackQuery, db):
    """Own completed payment details before the generic active-payment guard."""

    if await _render_if_completed(callback, db):
        return
    # Active payments retain the canonical reconciliation/provider lifecycle.
    await payment_archive_guard.guard_archived_payment_open(callback, db)


@router.callback_query(lambda c: str(c.data or "").startswith("pay_fake_success:"))
async def exact_archive_fake_payment(callback: CallbackQuery, db):
    """An old DEV button can never mutate a completed Case or lose its context."""

    if await _render_if_completed(callback, db):
        return
    await payment_archive_guard.guard_archived_fake_success(callback, db)


__all__ = ["router"]
