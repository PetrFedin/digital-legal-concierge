from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation


class LegacyConsultationOutcomeResolutionError(ValueError):
    pass


class LegacyConsultationOutcomeResolutionService:
    """Finish historical ``DONE + other`` M2 outcomes without generic status edits.

    New production flows no longer allow ``other``. This service exists only so
    records created by older releases can be deterministically closed, moved to
    M1, or turned into an explicit follow-up recommendation with a full audit
    trail. Valid modern outcomes are intentionally immutable here.
    """

    VALID_DECISIONS = frozenset({"close", "to_m1", "follow_up"})
    RESOLUTION_ACTION = "CONSULTATION_LEGACY_OUTCOME_RESOLVED"

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)
        self.notifications = NotificationEngine(db)

    async def _lock_case(self, case_id: int) -> Case:
        case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == int(case_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if case is None:
            raise LookupError("Дело не найдено")
        return case

    async def _latest_resolution_event(
        self,
        *,
        case_id: int,
        consultation_id: int,
    ) -> AuditLog | None:
        """Load exact historical-resolution provenance without an age window."""

        events = list(
            (
                await self.db.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "case",
                        AuditLog.entity_id == int(case_id),
                        AuditLog.action == self.RESOLUTION_ACTION,
                    )
                    .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                )
            ).scalars().all()
        )
        for event in events:
            value = event.new_value or {}
            try:
                event_consultation_id = int(value.get("consultation_id") or 0)
            except (TypeError, ValueError):
                event_consultation_id = 0
            if event_consultation_id == int(consultation_id):
                return event
        return None

    async def _require_exact_retry_or_conflict(
        self,
        *,
        consultation: Consultation,
        admin_id: int,
        decision: str,
        comment: str,
    ) -> Case:
        event = await self._latest_resolution_event(
            case_id=int(consultation.case_id),
            consultation_id=int(consultation.id),
        )
        value = event.new_value or {} if event is not None else {}
        event_decision = str(value.get("consultation_decision") or "").strip().lower()
        if (
            event is None
            or event.actor_id != int(admin_id)
            or str(event.comment or "").strip() != comment
            or event_decision != decision
        ):
            raise LegacyConsultationOutcomeResolutionError(
                "Старый результат уже завершён другим администратором, другим решением или с другим основанием. "
                "Старое действие не применено; обновите карточку."
            )
        return await self._lock_case(int(consultation.case_id))

    async def resolve(
        self,
        *,
        consultation_id: int,
        admin_id: int,
        decision: str,
        comment: str,
    ) -> tuple[Consultation, Case]:
        normalized_decision = str(decision or "").strip().lower()
        normalized_comment = str(comment or "").strip()
        if normalized_decision not in self.VALID_DECISIONS:
            raise LegacyConsultationOutcomeResolutionError(
                "Выберите конечное решение: close, to_m1 или follow_up"
            )
        if len(normalized_comment) < 10:
            raise LegacyConsultationOutcomeResolutionError(
                "Опишите основание решения минимум в 10 символах"
            )

        consultation = (
            await self.db.execute(
                select(Consultation)
                .where(Consultation.id == int(consultation_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if consultation is None:
            raise LookupError("Консультация не найдена")

        if str(consultation.status) != ConsultationStatus.DONE.value:
            raise LegacyConsultationOutcomeResolutionError(
                "Исправление доступно только для уже завершённой консультации"
            )
        current_decision = str(consultation.decision or "").strip().lower()
        if current_decision != "other":
            if current_decision == normalized_decision:
                case = await self._require_exact_retry_or_conflict(
                    consultation=consultation,
                    admin_id=admin_id,
                    decision=normalized_decision,
                    comment=normalized_comment,
                )
                return consultation, case
            raise LegacyConsultationOutcomeResolutionError(
                "У консультации уже есть другое поддерживаемое конечное решение. "
                "Старое действие не применено; обновите карточку."
            )

        case = await self._lock_case(int(consultation.case_id))
        if str(case.route or "") != "M2" or str(case.status) != CaseStatus.M2_CONSULTATION_DONE.value:
            raise LegacyConsultationOutcomeResolutionError(
                "Дело уже изменилось. Откройте актуальную карточку перед повторным решением"
            )

        old_value = {
            "consultation_status": str(consultation.status),
            "consultation_decision": current_decision,
            "case_status": str(case.status),
            "case_route": case.route,
            "next_action": case.next_action,
        }
        consultation.decision = normalized_decision

        if normalized_decision == "close":
            case.close_reason = "M2_LEGACY_OUTCOME_CLOSED"
            await self.cases.change_status(
                case=case,
                next_status=CaseStatus.M2_CLOSED,
                actor_type="admin",
                actor_id=admin_id,
                comment=normalized_comment,
            )
        elif normalized_decision == "to_m1":
            await self.cases.transfer_to_m1(
                case=case,
                actor_type="admin",
                actor_id=admin_id,
                comment=(
                    "Legacy-результат консультации уточнён: требуется ведение M1. "
                    + normalized_comment
                ),
            )
        else:
            # Follow-up stays in M2_CONSULTATION_DONE until the client accepts
            # the recommendation. The Telegram result screen will create the
            # next consultation only after an explicit client click.
            case.next_action = "Клиенту рекомендована повторная консультация"

        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=admin_id,
            case_id=case.id,
            action=self.RESOLUTION_ACTION,
            old_value=old_value,
            new_value={
                "consultation_id": consultation.id,
                "consultation_status": str(consultation.status),
                "consultation_decision": normalized_decision,
                "case_status": str(case.status),
                "case_route": case.route,
                "close_reason": case.close_reason,
                "next_action": case.next_action,
            },
            comment=normalized_comment,
        )
        await self.notifications.emit(
            event_code="CONSULTATION_COMPLETED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "decision": normalized_decision,
            },
            dedupe_key=(
                f"consultation:{consultation.id}:legacy-outcome:{normalized_decision}"
            ),
        )
        await self.db.flush()
        return consultation, case


__all__ = [
    "LegacyConsultationOutcomeResolutionError",
    "LegacyConsultationOutcomeResolutionService",
]
