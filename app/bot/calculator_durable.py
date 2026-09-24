from __future__ import annotations

import logging
from datetime import date, datetime

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.calculator_draft import CALCULATOR_CASE_ID
from app.bot.states import CalculatorStates
from app.domain.calculator.intake_service import CalculationIntakeService
from app.domain.calculator.penalty_calculator import parse_money
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case
from app.models.user import User

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
    "client_type",
    "deadline_confirmed",
    "unique_object",
    "ddu_signing_date",
    "acceptance_evasion",
    "object_transferred",
    "actual_transfer_date",
)
_CALLBACK_PREFIXES = {
    "calc_client_consumer:v2:": "client_consumer",
    "calc_client_other:v2:": "client_other",
    "calc_deadline_confirm_yes:v2:": "deadline_yes",
    "calc_deadline_confirm_review:v2:": "deadline_review",
    "calc_unique_yes:v2:": "unique_yes",
    "calc_unique_no:v2:": "unique_no",
    "calc_acceptance_evasion_no:v2:": "evasion_no",
    "calc_acceptance_evasion_yes:v2:": "evasion_yes",
    "calc_acceptance_evasion_unknown:v2:": "evasion_unknown",
    "calc_object_transferred_yes:v2:": "transfer_yes",
    "calc_object_transferred_no:v2:": "transfer_no",
    "calc_restart:v2:": "restart",
    # A committed result clears the active FSM binding. The explicit result
    # action still carries an exact Case id and is the authority to reopen only
    # that Case's intake before the canonical handler starts a fresh working set.
    "calc_repeat:v2:": "restart_explicit",
}


def _case_id(data: dict) -> int:
    try:
        value = int(data.get(CALCULATOR_CASE_ID) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _callback_case_id(raw: str, prefix: str) -> int:
    if not raw.startswith(prefix):
        return 0
    try:
        value = int(raw[len(prefix) :])
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _has_legacy_facts(data: dict) -> bool:
    return any(key in data for key in _CALCULATOR_FIELDS)


async def _warn_durable_failure(event) -> None:
    text = (
        "⚠️ Не удалось надёжно сохранить этот шаг в карточке обращения. "
        "Переход к следующему шагу не выполнен. Повторите действие — ранее "
        "сохранённые данные не удалены."
    )
    try:
        if isinstance(event, CallbackQuery) and event.message is not None:
            await event.message.answer(text)
        elif isinstance(event, Message):
            await event.answer(text)
    except Exception:
        logger.exception("Could not present durable calculator intake failure")


async def _owned_calculator_case(db, *, case_id: int, telegram_id: int) -> Case | None:
    """Lock the exact owned Case before accepting a durable questionnaire fact."""

    return (
        await db.execute(
            select(Case)
            .join(User, User.id == Case.client_id)
            .where(
                Case.id == int(case_id),
                User.telegram_id == int(telegram_id),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


class DurableCalculatorIntakeMiddleware:
    """Write accepted calculator facts to PostgreSQL before UI progression.

    This middleware deliberately runs *before* the canonical calculator handler
    for the small set of events that can add/change questionnaire facts. It
    mirrors the handler's syntactic validation, locks the exact owned Case, and
    commits the durable intake first. Only then may the handler update Redis/FSM
    and render the next screen.

    Invalid input is not persisted and is passed to the canonical handler so the
    existing validation copy remains the single presentation owner. A database
    failure is fail-closed: the handler is not called, therefore Telegram cannot
    advance while PostgreSQL still contains an older business state.

    For a pre-PM-017 session with no intake row, an existing Case-bound Redis
    draft may be imported once before the new accepted fact is written. Once a
    PostgreSQL intake exists, Redis is never allowed to overwrite it.
    """

    async def __call__(self, handler, event, data):
        state: FSMContext | None = data.get("state")
        db = data.get("db")
        if state is None or db is None:
            return await handler(event, data)

        before = dict(await state.get_data())
        state_case_id = _case_id(before)
        case_id = state_case_id
        mutation: tuple[str, object] | None = None
        current_state = await state.get_state()

        if isinstance(event, Message):
            if case_id <= 0:
                return await handler(event, data)
            if current_state == CalculatorStates.waiting_contract_price.state:
                try:
                    mutation = ("price", parse_money(event.text))
                except Exception:
                    return await handler(event, data)
            elif current_state == CalculatorStates.waiting_planned_transfer_date.state:
                try:
                    mutation = (
                        "planned_date",
                        datetime.strptime(str(event.text or "").strip(), "%d.%m.%Y").date(),
                    )
                except Exception:
                    return await handler(event, data)
            elif current_state == CalculatorStates.waiting_ddu_signing_date.state:
                try:
                    signed_date = datetime.strptime(
                        str(event.text or "").strip(), "%d.%m.%Y"
                    ).date()
                except Exception:
                    return await handler(event, data)
                if signed_date > date.today():
                    return await handler(event, data)
                mutation = ("ddu_signing_date", signed_date)
            elif current_state == CalculatorStates.waiting_actual_transfer_date.state:
                try:
                    actual_date = datetime.strptime(
                        str(event.text or "").strip(), "%d.%m.%Y"
                    ).date()
                except Exception:
                    return await handler(event, data)
                if actual_date > date.today():
                    return await handler(event, data)
                mutation = ("actual_date", actual_date)
            else:
                return await handler(event, data)

        elif isinstance(event, CallbackQuery):
            raw = str(event.data or "")
            for prefix, action in _CALLBACK_PREFIXES.items():
                callback_case_id = _callback_case_id(raw, prefix)
                if callback_case_id <= 0:
                    continue
                if action == "restart_explicit":
                    # A result-screen recalc is explicitly Case-bound even after
                    # finish_calculator_case removed the active FSM case id.
                    case_id = callback_case_id
                else:
                    # All in-questionnaire buttons must match the exact current
                    # Case or remain presentation-only stale callbacks.
                    if state_case_id <= 0 or callback_case_id != state_case_id:
                        return await handler(event, data)
                    case_id = state_case_id
                mutation = (action, callback_case_id)
                break
            if mutation is None:
                return await handler(event, data)
        else:
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        telegram_id = int(getattr(user, "id", 0) or 0)
        if telegram_id <= 0 or case_id <= 0:
            await _warn_durable_failure(event)
            return None

        try:
            case = await _owned_calculator_case(
                db,
                case_id=case_id,
                telegram_id=telegram_id,
            )
            if (
                case is None
                or str(case.status) not in _CALCULATOR_DRAFT_STATUSES
                or str(case.route or "") == "M2"
            ):
                await db.rollback()
                await _warn_durable_failure(event)
                return None

            service = CalculationIntakeService(db)
            intake = await service.get(case_id=case_id)
            if (
                intake is None
                and state_case_id == case_id
                and _has_legacy_facts(before)
            ):
                # One-time cutover only. Existing PostgreSQL state always wins.
                await service.sync_from_draft(
                    case_id=case_id,
                    data=before,
                    today=date.today(),
                )

            action, value = mutation
            if action == "price":
                await service.save_price(
                    case_id=case_id,
                    contract_price=value,
                )
            elif action == "planned_date":
                await service.save_planned_date(
                    case_id=case_id,
                    planned_transfer_date=value,
                    today=date.today(),
                )
            elif action == "client_consumer":
                await service.save_client_type(
                    case_id=case_id,
                    client_type="consumer",
                )
            elif action == "client_other":
                await service.save_client_type(
                    case_id=case_id,
                    client_type="business",
                )
            elif action == "deadline_yes":
                await service.save_deadline_confirmation(
                    case_id=case_id,
                    confirmed=True,
                )
            elif action == "deadline_review":
                await service.save_deadline_confirmation(
                    case_id=case_id,
                    confirmed=False,
                )
            elif action == "unique_yes":
                await service.save_unique_object(
                    case_id=case_id,
                    unique_object=True,
                )
            elif action == "unique_no":
                await service.save_unique_object(
                    case_id=case_id,
                    unique_object=False,
                )
            elif action == "ddu_signing_date":
                await service.save_ddu_signing_date(
                    case_id=case_id,
                    ddu_signing_date=value,
                    today=date.today(),
                )
            elif action == "evasion_no":
                await service.save_acceptance_evasion(
                    case_id=case_id,
                    value="no",
                )
            elif action == "evasion_yes":
                await service.save_acceptance_evasion(
                    case_id=case_id,
                    value="yes",
                )
            elif action == "evasion_unknown":
                await service.save_acceptance_evasion(
                    case_id=case_id,
                    value="unknown",
                )
            elif action == "transfer_yes":
                await service.save_transfer_status(
                    case_id=case_id,
                    object_transferred=True,
                )
            elif action == "transfer_no":
                await service.save_transfer_status(
                    case_id=case_id,
                    object_transferred=False,
                )
            elif action == "actual_date":
                await service.save_actual_date(
                    case_id=case_id,
                    actual_transfer_date=value,
                    today=date.today(),
                )
            elif action in {"restart", "restart_explicit"}:
                await service.reset(case_id=case_id)
            else:  # pragma: no cover - defensive closed world
                raise RuntimeError(f"Unsupported calculator intake mutation: {action}")

            # This commit is intentionally before handler/FSM/render. A later
            # Telegram failure can be recovered from PostgreSQL; the opposite
            # ordering would lose accepted business data after Redis loss.
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(
                "Durable calculator intake pre-commit failed for case_id=%s",
                case_id,
            )
            await _warn_durable_failure(event)
            return None

        return await handler(event, data)


__all__ = ["DurableCalculatorIntakeMiddleware"]
