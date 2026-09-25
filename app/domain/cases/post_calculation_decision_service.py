from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.calculator.calculator_service import CalculatorService
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models.case import Case


CHOICE_M1 = "m1"
CHOICE_SELF_FILING = "self_filing"
CHOICE_M2 = "m2"
CHOICE_POSTPONE = "postpone"
_ALLOWED_CHOICES = {
    CHOICE_M1,
    CHOICE_SELF_FILING,
    CHOICE_M2,
    CHOICE_POSTPONE,
}


class PostCalculationDecisionError(ValueError):
    pass


@dataclass(frozen=True)
class PostCalculationDecisionResult:
    case: Case
    choice: str
    outcome: str
    changed: bool


class PostCalculationDecisionService:
    """Apply a version-bound post-calculation choice under a case row lock.

    Telegram inline keyboards remain clickable long after they were rendered.
    Every mutating v2 callback therefore carries the case id that produced the
    screen. The service locks exactly that case and verifies ownership before a
    business transition, so a message from an older case can never mutate a new
    active case that happens to be on the same status.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)
        self.calculator = CalculatorService(db)

    async def _lock_case(self, *, client_id: int, case_id: int) -> Case | None:
        return (
            await self.db.execute(
                select(Case)
                .where(
                    Case.id == int(case_id),
                    Case.client_id == int(client_id),
                )
                .with_for_update()
            )
        ).scalars().first()

    @staticmethod
    def _status(case: Case) -> CaseStatus:
        return (
            case.status
            if isinstance(case.status, CaseStatus)
            else CaseStatus(str(case.status))
        )

    @staticmethod
    def _route(case: Case) -> str | None:
        value = getattr(case, "route", None)
        return str(value) if value not in (None, "") else None

    async def apply(
        self,
        *,
        client_id: int,
        case_id: int,
        choice: str,
    ) -> PostCalculationDecisionResult:
        normalized_choice = str(choice or "").strip().lower()
        if normalized_choice not in _ALLOWED_CHOICES:
            raise PostCalculationDecisionError("Неизвестный выбор после расчёта")

        case = await self._lock_case(client_id=client_id, case_id=case_id)
        if case is None:
            raise LookupError("Дело из этого экрана не найдено или недоступно")

        status = self._status(case)
        route = self._route(case)

        # Once a legal/consultation route has actually started, an old
        # post-calculation keyboard is informational only. It must never pull a
        # live case backwards or into the other service.
        if status.value.startswith("M1_") or route == RouteCode.M1.value:
            return PostCalculationDecisionResult(
                case=case,
                choice=normalized_choice,
                outcome="stale_m1",
                changed=False,
            )
        if status.value.startswith("M2_") or route == RouteCode.M2.value:
            return PostCalculationDecisionResult(
                case=case,
                choice=normalized_choice,
                outcome="stale_m2",
                changed=False,
            )
        if status not in {CaseStatus.CALCULATED, CaseStatus.CLIENT_DECISION}:
            return PostCalculationDecisionResult(
                case=case,
                choice=normalized_choice,
                outcome="stale_other",
                changed=False,
            )

        if normalized_choice in {CHOICE_M1, CHOICE_SELF_FILING}:
            # A stale/zero result must never open either paid M1 service. The
            # latest immutable calculation is the route-eligibility authority.
            await self.calculator.require_m1_eligible_calculation(case_id=int(case.id))
            requested_mode = (
                M1ServiceMode.SELF_FILING_PACKAGE.value
                if normalized_choice == CHOICE_SELF_FILING
                else M1ServiceMode.FULL_REPRESENTATION.value
            )
            mode_changed = str(case.service_mode or "") != requested_mode
            if mode_changed:
                old_mode = case.service_mode
                case.service_mode = requested_mode
                await add_case_history_event(
                    self.db,
                    actor_type="client",
                    actor_id=int(client_id),
                    case_id=int(case.id),
                    action="M1_SERVICE_MODE_SELECTED",
                    old_value={"service_mode": old_mode},
                    new_value={"service_mode": requested_mode},
                )
            outcome = (
                "self_filing_consent_required"
                if normalized_choice == CHOICE_SELF_FILING
                else "m1_consent_required"
            )
            if status == CaseStatus.CLIENT_DECISION:
                await self.db.flush()
                return PostCalculationDecisionResult(
                    case=case,
                    choice=normalized_choice,
                    outcome=outcome,
                    changed=mode_changed,
                )
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.CLIENT_DECISION,
                actor_type="client",
                actor_id=int(client_id),
                comment=(
                    "Клиент выбрал подготовку пакета для самостоятельной подачи. "
                    "Маршрут M1 ещё не начат: требуется отдельное подтверждение согласия."
                    if normalized_choice == CHOICE_SELF_FILING
                    else
                    "Клиент выбрал полное ведение дела после расчёта. "
                    "Маршрут M1 ещё не начат: требуется отдельное подтверждение согласия."
                ),
            )
            return PostCalculationDecisionResult(
                case=case,
                choice=normalized_choice,
                outcome=outcome,
                changed=True,
            )

        if normalized_choice == CHOICE_M2:
            case.service_mode = None
            await self.cases.transfer_to_m2(
                case=case,
                actor_type="client",
                actor_id=int(client_id),
                reason="Клиент явно выбрал консультацию после сохранённого расчёта",
            )
            return PostCalculationDecisionResult(
                case=case,
                choice=normalized_choice,
                outcome="m2_intake",
                changed=True,
            )

        # "Пока ничего не менять" never creates a service. If the client had
        # only opened the M1 consent step, return to the neutral calculated
        # state; the calculation itself remains stored.
        if status == CaseStatus.CLIENT_DECISION:
            case.service_mode = None
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.CALCULATED,
                actor_type="client",
                actor_id=int(client_id),
                comment=(
                    "Клиент отложил выбор маршрута после расчёта; услуга не начата, "
                    "расчёт сохранён."
                ),
            )
            return PostCalculationDecisionResult(
                case=case,
                choice=normalized_choice,
                outcome="postponed",
                changed=True,
            )

        return PostCalculationDecisionResult(
            case=case,
            choice=normalized_choice,
            outcome="postponed",
            changed=False,
        )


__all__ = [
    "CHOICE_M1",
    "CHOICE_SELF_FILING",
    "CHOICE_M2",
    "CHOICE_POSTPONE",
    "PostCalculationDecisionError",
    "PostCalculationDecisionResult",
    "PostCalculationDecisionService",
]
