from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domain.cases.case_service import CaseService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus


class M1ProcessError(ValueError):
    pass


class M1ProcessService:
    """Structured M1 legal-stage actions required by the frozen MVP."""

    def __init__(self, db):
        self.db = db
        self.cases = CaseService(db)
        self.payments = PaymentService(db)
        self.notifications = NotificationEngine(db)

    @staticmethod
    def _require_status(case, *allowed: CaseStatus) -> None:
        current = CaseStatus(str(case.status))
        if current not in allowed:
            expected = ", ".join(item.value for item in allowed)
            raise M1ProcessError(
                f"Действие недоступно из статуса {current.value}. Ожидается: {expected}"
            )

    @staticmethod
    def _reason(value: object, *, minimum: int = 10) -> str:
        reason = str(value or "").strip()
        if len(reason) < minimum:
            raise M1ProcessError(
                f"Укажите основание не короче {minimum} символов"
            )
        return reason

    async def mark_claim_sent(
        self,
        *,
        case,
        lawyer_id: int,
        comment: object,
    ):
        reason = self._reason(comment, minimum=5)
        self._require_status(
            case,
            CaseStatus.M1_POA_RECEIVED,
            CaseStatus.M1_CLAIM_PREPARATION,
        )
        if CaseStatus(str(case.status)) == CaseStatus.M1_POA_RECEIVED:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M1_CLAIM_PREPARATION,
                actor_type="lawyer",
                actor_id=lawyer_id,
                comment="Юрист начал подготовку претензии",
            )
        now = datetime.now(timezone.utc)
        case.claim_sent_at = now
        case.claim_waiting_until = now + timedelta(days=30)
        case.developer_response_status = "WAITING"
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_CLAIM_SENT,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=reason,
        )
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_WAITING_30_DAYS,
            actor_type="system",
            actor_id=None,
            comment="Запущен обязательный 30-дневный срок после направления претензии",
        )
        return case

    async def start_lawsuit_preparation(
        self,
        *,
        case,
        lawyer_id: int,
        comment: object,
    ):
        reason = self._reason(comment, minimum=5)
        self._require_status(case, CaseStatus.M1_WAITING_30_DAYS)
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_LAWSUIT_PREPARATION,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=reason,
        )
        return case

    async def mark_lawsuit_filed(
        self,
        *,
        case,
        lawyer_id: int,
        comment: object,
    ):
        reason = self._reason(comment, minimum=5)
        self._require_status(case, CaseStatus.M1_LAWSUIT_PREPARATION)
        case.lawsuit_filed_at = datetime.now(timezone.utc)
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_LAWSUIT_FILED,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=reason,
        )
        return case

    async def start_court_stage(
        self,
        *,
        case,
        lawyer_id: int,
        comment: object,
    ):
        reason = self._reason(comment, minimum=5)
        self._require_status(case, CaseStatus.M1_LAWSUIT_FILED)
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_COURT_STAGE,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=reason,
        )
        await self.notifications.emit(
            event_code="COURT_STAGE_STARTED",
            case_id=case.id,
            payload={"case_number": case.case_number},
            dedupe_key=f"case:{case.id}:court-stage",
        )
        return case

    async def record_court_decision(
        self,
        *,
        case,
        lawyer_id: int,
        comment: object,
    ):
        reason = self._reason(comment, minimum=10)
        self._require_status(case, CaseStatus.M1_COURT_STAGE)
        case.decision_date = datetime.now(timezone.utc)
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_DECISION_RECEIVED,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=reason,
        )
        payment = await self.payments.get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M1_COURT_PAYMENT,
        )
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_WAITING_PAYMENT_70000,
            actor_type="system",
            actor_id=None,
            comment="После решения суда открыт второй договорный платёж",
        )
        await self.notifications.emit(
            event_code="M1_COURT_DECISION_RECEIVED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "amount": str(payment.amount),
            },
            dedupe_key=f"case:{case.id}:court-decision",
        )
        return case

    async def close_after_success_fee(
        self,
        *,
        case,
        actor_type: str,
        actor_id: int | None,
        reason: object,
    ):
        normalized = self._reason(reason, minimum=10)
        self._require_status(case, CaseStatus.M1_SUCCESS_FEE_RECEIVED)
        case.closure_reason = normalized
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_CLOSED,
            actor_type=actor_type,
            actor_id=actor_id,
            comment=normalized,
        )
        await self.notifications.emit(
            event_code="M1_CASE_CLOSED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "reason": normalized,
            },
            dedupe_key=f"case:{case.id}:closed",
        )
        return case

    async def close_from_review(
        self,
        *,
        case,
        lawyer_id: int,
        reason: object,
    ):
        normalized = self._reason(reason, minimum=10)
        self._require_status(
            case,
            CaseStatus.M1_LAWYER_REVIEW,
            CaseStatus.M1_DOCS_REQUESTED,
        )
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_REJECTED,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=normalized,
        )
        case.closure_reason = normalized
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_CLOSED,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=normalized,
        )
        await self.notifications.emit(
            event_code="M1_CASE_CLOSED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "reason": normalized,
            },
            dedupe_key=f"case:{case.id}:closed",
        )
        return case
