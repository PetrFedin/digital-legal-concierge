from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lawyer import assigned_case, assert_case_snapshot
from app.db.session import get_db
from app.domain.cases.case_service import CaseService
from app.domain.cases.sla_service import CaseSLAError, CaseSLAService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(tags=["lawyer-poa"])


@router.post("/lawyer/cases/{case_id}/poa/received")
async def confirm_poa_received(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Confirm actual POA receipt before the claim stage can be opened."""

    actor = await require_lawyer_actor(db, x_admin_token)
    comment = str(payload.get("comment") or "").strip()
    if len(comment) < 5:
        raise HTTPException(
            status_code=400,
            detail=(
                "Укажите, как подтверждено получение доверенности — минимум 5 символов"
            ),
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
        if str(case.status) != CaseStatus.M1_POWER_OF_ATTORNEY.value:
            raise ValueError(
                "Получение доверенности можно подтвердить только на этапе оформления доверенности"
            )

        source_version = case.updated_at.isoformat()
        await CaseService(db).change_status(
            case=case,
            next_status=CaseStatus.M1_POA_RECEIVED,
            actor_type="lawyer",
            actor_id=actor.lawyer.id,
            comment=comment,
        )
        await CaseSLAService(db).record_lawyer_activity(
            case=case,
            lawyer_id=actor.lawyer.id,
            action="M1_POA_RECEIVED_CONFIRMED",
            comment=comment,
        )
        await NotificationEngine(db).emit(
            event_code="M1_POA_RECEIVED_CONFIRMED",
            case_id=case.id,
            user_id=case.client_id,
            payload={"case_number": case.case_number},
            dedupe_key=f"case:{case.id}:poa-received:{source_version}",
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
    }
