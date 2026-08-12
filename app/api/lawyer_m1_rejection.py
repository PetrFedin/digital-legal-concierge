from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lawyer import assert_case_snapshot, assigned_case
from app.db.session import get_db
from app.domain.cases.case_service import CaseService
from app.domain.cases.sla_service import CaseSLAError, CaseSLAService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(tags=["lawyer-m1-rejection"])


@router.post("/lawyer/cases/{case_id}/reject")
async def reject_m1_case(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Refuse full M1 representation without silently choosing M2 for the client.

    A transfer-to-M2 action means the lawyer has selected consultation as the
    route. Rejection is different: it records that full M1 representation is not
    accepted and gives the client the explicit choice to continue as M2 or close
    the request. The client-side recovery flow owns that next decision.
    """

    actor = await require_lawyer_actor(db, x_admin_token)
    reason = " ".join(str(payload.get("reason") or "").split())
    if len(reason) < 10:
        raise HTTPException(
            status_code=400,
            detail="Укажите содержательную причину отказа минимум в 10 символах",
        )

    try:
        case = await assigned_case(
            db,
            case_id,
            actor.lawyer.id,
            for_update=True,
        )
        assert_case_snapshot(
            case,
            expected_status=payload.get("expected_status"),
            expected_updated_at=payload.get("expected_updated_at"),
        )
        if case.status not in {
            CaseStatus.M1_LAWYER_REVIEW,
            CaseStatus.M1_DOCS_REQUESTED,
        }:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Отказ в полном ведении доступен только на этапе проверки дела. "
                    "Обновите карточку."
                ),
            )

        source_version = case.updated_at.isoformat()
        await CaseService(db).change_status(
            case=case,
            next_status=CaseStatus.M1_REJECTED,
            actor_type="lawyer",
            actor_id=actor.lawyer.id,
            comment=reason,
        )
        await CaseSLAService(db).record_lawyer_activity(
            case=case,
            lawyer_id=actor.lawyer.id,
            action="M1_CASE_REJECTED",
            comment=reason,
        )
        await NotificationEngine(db).emit(
            event_code="M1_CASE_REJECTED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "reason": reason,
                "next_action": (
                    "Откройте «Моё дело» и выберите консультацию или завершение обращения"
                ),
            },
            dedupe_key=f"case:{case.id}:lawyer-reject:{source_version}",
        )
        await db.commit()
        await db.refresh(case)
    except HTTPException:
        await db.rollback()
        raise
    except (ValueError, CaseSLAError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise

    return {
        "ok": True,
        "case_id": case.id,
        "status": case.status,
        "next_action": case.next_action,
        "updated_at": case.updated_at.isoformat(),
        "sla_status": case.sla_status,
        "sla_due_at": case.sla_due_at,
    }
