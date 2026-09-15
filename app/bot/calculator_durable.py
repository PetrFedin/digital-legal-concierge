from __future__ import annotations

import logging
from datetime import date

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.calculator_draft import CALCULATOR_CASE_ID
from app.db.session import AsyncSessionLocal
from app.domain.calculator.intake_service import CalculationIntakeService
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case

logger = logging.getLogger(__name__)

_CALCULATOR_DRAFT_STATUSES = {
    CaseStatus.NEW.value,
    CaseStatus.CALCULATOR_STARTED.value,
    CaseStatus.CALCULATED.value,
    CaseStatus.CLIENT_DECISION.value,
}
_CALCULATOR_FIELDS = (
    "contract_price",
    "planned_transfer_date",
    "object_transferred",
    "actual_transfer_date",
)


def _case_id(data: dict) -> int:
    try:
        value = int(data.get(CALCULATOR_CASE_ID) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _durable_snapshot(data: dict) -> dict:
    snapshot = {CALCULATOR_CASE_ID: _case_id(data)}
    for key in _CALCULATOR_FIELDS:
        if key in data:
            snapshot[key] = data[key]
    return snapshot


async def _warn_durable_failure(event) -> None:
    text = (
        "⚠️ Текущий шаг остался в сессии Telegram, но не удалось подтвердить его "
        "сохранение в карточке обращения. Не закрывайте диалог и повторите действие."
    )
    try:
        if isinstance(event, CallbackQuery) and event.message is not None:
            await event.message.answer(text)
        elif isinstance(event, Message):
            await event.answer(text)
    except Exception:
        logger.exception("Could not present durable calculator intake failure")


class DurableCalculatorIntakeMiddleware:
    """Mirror accepted calculator FSM facts into the Case database record.

    Canonical handlers still own validation, Case provenance and UI. This
    middleware runs only after a handler returns successfully, then persists the
    resulting exact Case-bound working set in a separate short transaction. The
    transaction locks the Case row so two Telegram deliveries cannot race the
    one-row-per-Case intake into contradictory snapshots.

    Only the *post-handler* active calculator working set is mirrored. When a
    completed calculation clears its FSM Case binding, CalculatorService's same-
    transaction completed intake remains authoritative and cannot be overwritten
    by the pre-handler Redis snapshot.
    """

    async def __call__(self, handler, event, data):
        state: FSMContext | None = data.get("state")
        if state is None:
            return await handler(event, data)

        result = await handler(event, data)
        after = dict(await state.get_data())
        case_id = _case_id(after)
        if case_id <= 0:
            return result

        try:
            async with AsyncSessionLocal() as db:
                case = (
                    await db.execute(
                        select(Case)
                        .where(Case.id == case_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if case is None:
                    return result
                if str(case.status) not in _CALCULATOR_DRAFT_STATUSES:
                    return result
                if str(case.route or "") == "M2":
                    return result

                await CalculationIntakeService(db).sync_from_draft(
                    case_id=case_id,
                    data=_durable_snapshot(after),
                    today=date.today(),
                )
                await db.commit()
        except Exception:
            logger.exception(
                "Durable calculator intake sync failed for case_id=%s",
                case_id,
            )
            await _warn_durable_failure(event)

        return result


__all__ = ["DurableCalculatorIntakeMiddleware"]
