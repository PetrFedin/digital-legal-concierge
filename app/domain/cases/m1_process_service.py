from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.models.court_event import CourtEvent


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

    @staticmethod
    def _event_date(value: object) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value or "").strip()
            if not raw:
                raise M1ProcessError("Укажите дату судебного события")
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as error:
                raise M1ProcessError(
                    "Дата судебного события должна быть в ISO-формате"
                ) from error
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _optional_text(value: object, *, limit: int) -> str | None:
        text = " ".join(str(value or "").split())
        if not text:
            return None
        if len(text) > limit:
            raise M1ProcessError(f"Текст длиннее допустимых {limit} символов")
        return text

    async def record_court_event(
        self,
        *,
        case,
        lawyer_id: int,
        event_type: object,
        event_date: object,
        court_name: object | None = None,
        court_number: object | None = None,
        result: object | None = None,
        client_comment: object | None = None,
        attachments_note: object | None = None,
    ):
        normalized_type = str(event_type or "").strip().lower()
        if normalized_type not in {
            "hearing",
            "lawsuit_filed",
            "court_started",
            "decision",
            "other",
        }:
            raise M1ProcessError("Недопустимый тип судебного события")
        self._require_status(
            case,
            CaseStatus.M1_LAWSUIT_FILED,
            CaseStatus.M1_COURT_STAGE,
        )
        event_when = self._event_date(event_date)
        court = self._optional_text(court_name, limit=255)
        number = self._optional_text(court_number, limit=255)
        internal_result = self._optional_text(result, limit=2000)
        safe_comment = self._optional_text(client_comment, limit=500)
        attachments = self._optional_text(attachments_note, limit=1000)

        event = CourtEvent(
            case_id=case.id,
            lawyer_id=lawyer_id,
            event_type=normalized_type,
            event_date=event_when,
            court_name=court,
            court_number=number,
            result=internal_result,
            client_comment=safe_comment,
            attachments_note=attachments,
        )
        self.db.add(event)
        await self.db.flush()

        if CaseStatus(str(case.status)) == CaseStatus.M1_LAWSUIT_FILED:
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M1_COURT_STAGE,
                actor_type="lawyer",
                actor_id=lawyer_id,
                comment="Зафиксировано первое судебное событие",
            )
            await self.notifications.emit(
                event_code="COURT_STAGE_STARTED",
                case_id=case.id,
                payload={"case_number": case.case_number},
                dedupe_key=f"case:{case.id}:court-stage",
            )

        await add_case_history_event(
            self.db,
            actor_type="lawyer",
            actor_id=lawyer_id,
            case_id=case.id,
            action="COURT_EVENT_ADDED",
            new_value={
                "court_event_id": event.id,
                "event_type": normalized_type,
                "event_date": event_when.isoformat(),
                "court_name": court,
                "court_number": number,
                "client_comment": safe_comment,
                "attachments_present": bool(attachments),
            },
            comment=internal_result,
        )

        if normalized_type == "decision":
            if CaseStatus(str(case.status)) != CaseStatus.M1_COURT_STAGE:
                raise M1ProcessError(
                    "Решение суда можно фиксировать только на судебном этапе"
                )
            await self.record_court_decision(
                case=case,
                lawyer_id=lawyer_id,
                comment=safe_comment or internal_result or "Решение суда получено",
                decision_date=event_when,
            )
        return event

    async def list_court_events(self, case_id: int) -> list[CourtEvent]:
        return list(
            (
                await self.db.execute(
                    select(CourtEvent)
                    .where(CourtEvent.case_id == int(case_id))
                    .order_by(CourtEvent.event_date.desc(), CourtEvent.id.desc())
                )
            ).scalars().all()
        )

    async def record_court_decision(
        self,
        *,
        case,
        lawyer_id: int,
        comment: object,
        decision_date: datetime | None = None,
    ):
        reason = self._reason(comment, minimum=10)
        self._require_status(case, CaseStatus.M1_COURT_STAGE)
        case.decision_date = decision_date or datetime.now(timezone.utc)
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
