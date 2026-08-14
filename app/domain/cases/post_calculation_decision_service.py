from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models.case import Case


CHOICE_M1 = "m1"
CHOICE_M2 = "m2"
CHOICE_POSTPONE = "postpone"
_ALLOWED_CHOICES = {CHOICE_M1, CHOICE_M2, CHOICE_POSTPONE}
_TERMINAL_STATUSES = {
    CaseStatus.M1_CLOSED,
    CaseStatus.M2_CLOSED,
    CaseStatus.ARCHIVED,
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
    """Apply the three post-calculation choices under one case row lock.

    Telegram inline keyboards remain clickable long after they were rendered and
    users can also double-tap different buttons. The current case is therefore
    re-read under ``FOR UPDATE`` immediately before any route mutation. Business
    transitions are delegated to CaseService so history, SLA and transition
    policy stay identical to the rest of the product.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)

    async def _lock_active_case(self, *, client_id: int) -> Case | None:
        return (
            await self.db.execute(
                select(Case)
                .where(Case.client_id == int(client_id))
                .where(Case.status.notin_(tuple(_TERMINAL_STATUSES)))
                .order_by(Case.created_at.desc(), Case.id.desc())
                .limit(1)
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
        choice: str,
    ) -> PostCalculationDecisionResult:
        normalized_choice = str(choice or "").strip().lower()
        if normalized_choice not in _ALLOWED_CHOICES:
            raise PostCalculationDecisionError("Неизвестный выбор после расчёта")

        case = await self._lock_active_case(client_id=client_id)
        if case is None:
            raise LookupError("Активное дело не найдено")

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

        if normalized_choice == CHOICE_M1:
            if status == CaseStatus.CLIENT_DECISION:
                return PostCalculationDecisionResult(
                    case=case,
                    choice=normalized_choice,
                    outcome="m1_consent_required",
                    changed=False,
                )
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.CLIENT_DECISION,
                actor_type="client",
                actor_id=int(client_id),
                comment=(
                    "Клиент выбрал ведение дела после расчёта. "
                    "Маршрут M1 ещё не начат: требуется отдельное подтверждение согласия."
                ),
            )
            return PostCalculationDecisionResult(
                case=case,
                choice=normalized_choice,
                outcome="m1_consent_required",
                changed=True,
            )

        if normalized_choice == CHOICE_M2:
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
    "CHOICE_M2",
    "CHOICE_POSTPONE",
    "PostCalculationDecisionError",
    "PostCalculationDecisionResult",
    "PostCalculationDecisionService",
]
