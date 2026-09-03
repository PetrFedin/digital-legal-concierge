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
    """Serialize a version-bound client decision after a lawyer rejects M1."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)

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

    async def apply(
        self,
        *,
        client_id: int,
        case_id: int,
        decision: str,
    ) -> M1RejectionDecisionResult:
        normalized = str(decision or "").strip().lower()
        if normalized not in {DECISION_TO_M2, DECISION_CLOSE}:
            raise M1RejectionDecisionError("Неизвестное решение после отказа M1")

        case = await self._lock_case(client_id=client_id, case_id=case_id)
        if case is None:
            raise LookupError("Дело из этого сообщения не найдено или недоступно")

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
