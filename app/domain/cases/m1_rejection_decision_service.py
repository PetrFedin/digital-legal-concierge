from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case


DECISION_TO_M2 = "to_m2"
DECISION_CLOSE = "close"


class M1RejectionDecisionError(ValueError):
    pass


@dataclass(frozen=True)
class M1RejectionDecisionResult:
    case: Case
    decision: str
    outcome: str
    changed: bool


class M1RejectionDecisionService:
    """Serialize mutually exclusive client decisions after a lawyer rejects M1."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)

    async def _lock_latest_case(self, *, client_id: int) -> Case | None:
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
    ) -> M1RejectionDecisionResult:
        normalized = str(decision or "").strip().lower()
        if normalized not in {DECISION_TO_M2, DECISION_CLOSE}:
            raise M1RejectionDecisionError("Неизвестное решение после отказа M1")

        case = await self._lock_latest_case(client_id=client_id)
        if case is None:
            raise LookupError("Активное дело не найдено")

        status = self._status(case)
        if status != CaseStatus.M1_REJECTED:
            return M1RejectionDecisionResult(
                case=case,
                decision=normalized,
                outcome="stale",
                changed=False,
            )

        if normalized == DECISION_TO_M2:
            await self.cases.transfer_to_m2(
                case=case,
                actor_type="client",
                actor_id=int(client_id),
                reason="Клиент выбрал консультацию после отказа в полном ведении M1",
            )
            return M1RejectionDecisionResult(
                case=case,
                decision=normalized,
                outcome="m2_intake",
                changed=True,
            )

        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_CLOSED,
            actor_type="client",
            actor_id=int(client_id),
            comment="Клиент завершил обращение после отказа в полном ведении M1",
        )
        return M1RejectionDecisionResult(
            case=case,
            decision=normalized,
            outcome="closed",
            changed=True,
        )


__all__ = [
    "DECISION_CLOSE",
    "DECISION_TO_M2",
    "M1RejectionDecisionError",
    "M1RejectionDecisionResult",
    "M1RejectionDecisionService",
]
