from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case


CONSENT_ACCEPT = "accept"
CONSENT_DECLINE = "decline"


class ConsentDecisionError(ValueError):
    pass


@dataclass(frozen=True)
class ConsentDecisionResult:
    case: Case
    decision: str
    outcome: str
    changed: bool


class ConsentDecisionService:
    """Serialize consent acceptance/decline against post-calculation route choice."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)

    async def _lock_latest_active_case(self, *, client_id: int) -> Case | None:
        return (
            await self.db.execute(
                select(Case)
                .where(Case.client_id == int(client_id))
                .where(
                    Case.status.notin_(
                        (
                            CaseStatus.M1_CLOSED,
                            CaseStatus.M2_CLOSED,
                            CaseStatus.ARCHIVED,
                        )
                    )
                )
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

    async def apply(
        self,
        *,
        client_id: int,
        decision: str,
    ) -> ConsentDecisionResult:
        normalized = str(decision or "").strip().lower()
        if normalized not in {CONSENT_ACCEPT, CONSENT_DECLINE}:
            raise ConsentDecisionError("Неизвестное решение по согласию")

        case = await self._lock_latest_active_case(client_id=client_id)
        if case is None:
            raise LookupError("Активное дело не найдено")

        status = self._status(case)
        if status.value.startswith("M1_"):
            return ConsentDecisionResult(case, normalized, "stale_m1", False)
        if status.value.startswith("M2_"):
            return ConsentDecisionResult(case, normalized, "stale_m2", False)
        if status == CaseStatus.CALCULATED:
            return ConsentDecisionResult(
                case,
                normalized,
                "route_not_selected" if normalized == CONSENT_ACCEPT else "declined",
                False,
            )
        if status != CaseStatus.CLIENT_DECISION:
            return ConsentDecisionResult(case, normalized, "stale_other", False)

        if normalized == CONSENT_ACCEPT:
            await self.cases.transfer_to_m1(
                case=case,
                actor_type="client",
                actor_id=int(client_id),
                comment=(
                    "Клиент отдельно и явно подтвердил согласие на обработку персональных данных "
                    "для маршрута ведения дела M1"
                ),
            )
            return ConsentDecisionResult(case, normalized, "accepted", True)

        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.CALCULATED,
            actor_type="client",
            actor_id=int(client_id),
            comment=(
                "Клиент явно подтвердил отказ от согласия для маршрута M1; "
                "предварительный расчёт сохранён"
            ),
        )
        return ConsentDecisionResult(case, normalized, "declined", True)


__all__ = [
    "CONSENT_ACCEPT",
    "CONSENT_DECLINE",
    "ConsentDecisionError",
    "ConsentDecisionResult",
    "ConsentDecisionService",
]
