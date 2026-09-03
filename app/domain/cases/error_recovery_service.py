from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.cases.case_transition_policy import ERROR_RECOVERY_TARGETS
from app.domain.statuses.case_statuses import CaseStatus
from app.models.audit_log import AuditLog
from app.models.case import Case


# Compatibility alias for existing tests/importers. The transition policy is the
# single owner of the allowed ERROR recovery target set.
SAFE_ERROR_RECOVERY_TARGETS = ERROR_RECOVERY_TARGETS


@dataclass(frozen=True)
class ErrorRecoverySuggestion:
    status: CaseStatus
    route: str
    audit_event_id: int
    occurred_at: datetime | None
    source: str


class CaseErrorRecoveryError(ValueError):
    pass


class CaseErrorRecoveryService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)

    @staticmethod
    def _status(value: object) -> CaseStatus | None:
        try:
            return value if isinstance(value, CaseStatus) else CaseStatus(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _candidate_from_event(row: AuditLog) -> CaseStatus | None:
        old_value = row.old_value if isinstance(row.old_value, dict) else {}
        new_value = row.new_value if isinstance(row.new_value, dict) else {}
        if str(new_value.get("status") or "") == CaseStatus.ERROR.value:
            return CaseErrorRecoveryService._status(old_value.get("status"))
        return None

    async def suggest(self, case_id: int) -> ErrorRecoverySuggestion | None:
        rows = list(
            (
                await self.db.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "case",
                        AuditLog.entity_id == int(case_id),
                    )
                    .order_by(AuditLog.id.desc())
                    .limit(100)
                )
            ).scalars().all()
        )

        # Preferred source: the audited transition into ERROR. This proves the
        # exact previous case state instead of guessing from route or UI text.
        for row in rows:
            candidate = self._candidate_from_event(row)
            if candidate in ERROR_RECOVERY_TARGETS:
                route = "M1" if candidate.value.startswith("M1_") else "M2"
                return ErrorRecoverySuggestion(
                    status=candidate,
                    route=route,
                    audit_event_id=int(row.id),
                    occurred_at=row.created_at,
                    source="error_transition",
                )

        # If ERROR was written by old code without an explicit status-change
        # event, expose no automatic recovery. A financial/consultation state is
        # never reconstructed from a loose historical guess.
        return None

    async def recover_last_safe_status(
        self,
        *,
        case_id: int,
        actor_id: int | None,
        expected_updated_at: str | None,
        comment: str,
    ) -> tuple[Case, ErrorRecoverySuggestion]:
        clean_comment = " ".join(str(comment or "").split())
        if len(clean_comment) < 10:
            raise CaseErrorRecoveryError(
                "Опишите причину восстановления минимум в 10 символах"
            )

        case = (
            await self.db.execute(
                select(Case).where(Case.id == int(case_id)).with_for_update()
            )
        ).scalar_one_or_none()
        if case is None:
            raise LookupError("Дело не найдено")
        if self._status(case.status) != CaseStatus.ERROR:
            raise CaseErrorRecoveryError(
                "Дело уже вышло из технического статуса. Обновите карточку."
            )
        if expected_updated_at is not None and case.updated_at.isoformat() != str(
            expected_updated_at
        ):
            raise CaseErrorRecoveryError(
                "Дело изменилось после загрузки экрана. Обновите карточку перед восстановлением."
            )

        suggestion = await self.suggest(case.id)
        if suggestion is None:
            raise CaseErrorRecoveryError(
                "Безопасный предыдущий этап не подтверждён аудитом. Автоматическое восстановление запрещено."
            )

        error_snapshot = {
            "status": str(case.status),
            "route": case.route,
            "next_action": case.next_action,
            "updated_at": case.updated_at.isoformat(),
        }
        await self.cases.change_status(
            case=case,
            next_status=suggestion.status,
            actor_type="admin",
            actor_id=actor_id,
            comment=(
                f"Восстановление из ERROR по аудиту #{suggestion.audit_event_id}. "
                f"{clean_comment}"
            ),
        )
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=case.id,
            action="CASE_ERROR_RECOVERED_TO_LAST_SAFE_STATUS",
            old_value=error_snapshot,
            new_value={
                "status": str(case.status),
                "route": case.route,
                "next_action": case.next_action,
                "source_audit_event_id": suggestion.audit_event_id,
                "recovery_policy": "last_audited_safe_case_stage",
            },
            comment=clean_comment,
        )
        await self.db.flush()
        return case, suggestion
