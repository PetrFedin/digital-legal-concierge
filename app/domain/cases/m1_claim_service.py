from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_service import CaseService
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.models.audit_log import AuditLog
from app.models.case import Case

CLAIM_WAIT_DAYS = 30


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class CourtEligibility:
    eligible: bool
    wait_started_at: datetime | None
    due_at: datetime | None
    remaining_seconds: int
    reason: str | None = None


class M1ClaimService:
    """Explicit lawyer-owned M1 transitions from POA through court payment.

    Named actions replace generic status mutation. The 30-day clock is anchored
    to the immutable audit event that entered M1_WAITING_30_DAYS, not to
    Case.updated_at, which may change for unrelated case activity.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)

    @staticmethod
    def _status(case: Case) -> CaseStatus:
        try:
            return CaseStatus(str(case.status))
        except ValueError as error:
            raise ValueError(f"Неизвестный статус дела: {case.status}") from error

    @staticmethod
    def _assert_assigned(case: Case, lawyer_id: int) -> None:
        if int(case.assigned_lawyer_id or 0) != int(lawyer_id):
            raise ValueError("Дело не назначено текущему юристу")

    async def start_claim_preparation(
        self,
        *,
        case: Case,
        lawyer_id: int,
        comment: str | None = None,
    ) -> Case:
        self._assert_assigned(case, lawyer_id)
        if self._status(case) != CaseStatus.M1_POA_RECEIVED:
            raise ValueError(
                "Подготовку претензии можно начать только после подтверждения доверенности"
            )
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_CLAIM_PREPARATION,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=comment or "Юрист начал подготовку претензии",
        )
        return case

    async def mark_claim_sent(
        self,
        *,
        case: Case,
        lawyer_id: int,
        comment: str,
    ) -> Case:
        self._assert_assigned(case, lawyer_id)
        if self._status(case) != CaseStatus.M1_CLAIM_PREPARATION:
            raise ValueError(
                "Отправку претензии можно фиксировать только после этапа её подготовки"
            )
        clean_comment = str(comment or "").strip()
        if len(clean_comment) < 5:
            raise ValueError(
                "Укажите способ отправки или реквизиты подтверждения — минимум 5 символов"
            )
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_CLAIM_SENT,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=clean_comment,
        )
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_WAITING_30_DAYS,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=(
                "Контрольный 30-дневный срок начат после зафиксированной отправки "
                f"претензии. {clean_comment}"
            ),
        )
        return case

    async def claim_wait_started_at(self, case: Case) -> datetime | None:
        events = list(
            (
                await self.db.execute(
                    select(AuditLog)
                    .where(AuditLog.entity_type == "case")
                    .where(AuditLog.entity_id == case.id)
                    .where(AuditLog.action == "CASE_STATUS_CHANGED")
                    .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                )
            ).scalars().all()
        )
        for event in events:
            value = event.new_value or {}
            if str(value.get("status") or "") == CaseStatus.M1_WAITING_30_DAYS:
                return as_utc(event.created_at)
        if self._status(case) == CaseStatus.M1_WAITING_30_DAYS and case.updated_at:
            # Legacy fallback for records created before the dedicated history
            # invariant existed. New writes always have the audit event above.
            return as_utc(case.updated_at)
        return None

    async def court_eligibility(
        self,
        *,
        case: Case,
        now: datetime | None = None,
    ) -> CourtEligibility:
        if self._status(case) != CaseStatus.M1_WAITING_30_DAYS:
            return CourtEligibility(
                eligible=False,
                wait_started_at=None,
                due_at=None,
                remaining_seconds=0,
                reason="Дело не находится на 30-дневном сроке ожидания",
            )
        started_at = await self.claim_wait_started_at(case)
        if started_at is None:
            return CourtEligibility(
                eligible=False,
                wait_started_at=None,
                due_at=None,
                remaining_seconds=0,
                reason="Не найдено начало 30-дневного срока",
            )
        due_at = started_at + timedelta(days=CLAIM_WAIT_DAYS)
        current = as_utc(now or datetime.now(timezone.utc))
        remaining = max(0, int((due_at - current).total_seconds()))
        if current < due_at:
            return CourtEligibility(
                eligible=False,
                wait_started_at=started_at,
                due_at=due_at,
                remaining_seconds=remaining,
                reason="30-дневный срок ещё не истёк",
            )
        return CourtEligibility(
            eligible=True,
            wait_started_at=started_at,
            due_at=due_at,
            remaining_seconds=0,
        )

    async def open_court_stage(
        self,
        *,
        case: Case,
        lawyer_id: int,
        comment: str,
        now: datetime | None = None,
    ) -> Case:
        self._assert_assigned(case, lawyer_id)
        clean_comment = str(comment or "").strip()
        if len(clean_comment) < 5:
            raise ValueError("Укажите основание открытия судебного этапа")
        eligibility = await self.court_eligibility(case=case, now=now)
        if not eligibility.eligible:
            due = (
                eligibility.due_at.strftime("%d.%m.%Y %H:%M UTC")
                if eligibility.due_at
                else "не определён"
            )
            raise ValueError(
                f"Судебный этап пока недоступен: {eligibility.reason}. Срок: {due}"
            )
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_COURT_STAGE,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=clean_comment,
        )
        return case

    async def open_court_payment(
        self,
        *,
        case: Case,
        lawyer_id: int,
        comment: str,
    ) -> Case:
        """Record the court-stage decision and create the second payment due."""

        self._assert_assigned(case, lawyer_id)
        if self._status(case) != CaseStatus.M1_COURT_STAGE:
            raise ValueError(
                "Второй платёж можно открыть только после начала судебного этапа"
            )
        clean_comment = str(comment or "").strip()
        if len(clean_comment) < 5:
            raise ValueError(
                "Укажите судебное событие или основание открытия второго платежа"
            )
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_WAITING_PAYMENT_70000,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=clean_comment,
        )
        # Create the financial obligation at the moment it becomes due. Online
        # mode may later attach a provider URL to this same record; disabled mode
        # can confirm the same pending record after verified bank/offline receipt.
        await PaymentService(self.db).get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M1_COURT_PAYMENT,
        )
        return case
