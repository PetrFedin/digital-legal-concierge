from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lawyer import assigned_case, assert_case_snapshot
from app.db.session import get_db
from app.domain.cases.m1_claim_service import M1ClaimService
from app.domain.cases.sla_service import CaseSLAError, CaseSLAService
from app.domain.notifications.notification_engine import NotificationEngine
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(prefix="/lawyer", tags=["lawyer-m1-claim"])


def _snapshot(payload: dict | None) -> tuple[str | None, object | None, object | None]:
    body = payload or {}
    comment = str(body.get("comment") or "").strip() or None
    return comment, body.get("expected_status"), body.get("expected_updated_at")


@router.post("/cases/{case_id}/claim/start")
async def start_claim_preparation(
    case_id: int,
    payload: dict | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    comment, expected_status, expected_updated_at = _snapshot(payload)
    try:
        case = await assigned_case(
            db,
            case_id,
            actor.lawyer.id,
            for_update=True,
        )
        assert_case_snapshot(
            case,
            expected_status=expected_status,
            expected_updated_at=expected_updated_at,
        )
        source_version = case.updated_at.isoformat()
        await M1ClaimService(db).start_claim_preparation(
            case=case,
            lawyer_id=actor.lawyer.id,
            comment=comment,
        )
        await CaseSLAService(db).record_lawyer_activity(
            case=case,
            lawyer_id=actor.lawyer.id,
            action="M1_CLAIM_PREPARATION_STARTED",
            comment=comment,
        )
        await NotificationEngine(db).emit(
            event_code="M1_CLAIM_PREPARATION_STARTED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "next_action": case.next_action or "Ожидать отправки претензии",
            },
            dedupe_key=f"case:{case.id}:claim-preparation:{source_version}",
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


@router.post("/cases/{case_id}/claim/sent")
async def mark_claim_sent(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    comment, expected_status, expected_updated_at = _snapshot(payload)
    if not comment or len(comment) < 5:
        raise HTTPException(
            status_code=400,
            detail="Укажите способ отправки или реквизиты подтверждения — минимум 5 символов",
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
            expected_status=expected_status,
            expected_updated_at=expected_updated_at,
        )
        source_version = case.updated_at.isoformat()
        service = M1ClaimService(db)
        await service.mark_claim_sent(
            case=case,
            lawyer_id=actor.lawyer.id,
            comment=comment,
        )
        eligibility = await service.court_eligibility(case=case)
        due_at = eligibility.due_at
        await CaseSLAService(db).record_lawyer_activity(
            case=case,
            lawyer_id=actor.lawyer.id,
            action="M1_CLAIM_SENT",
            comment=comment,
        )
        await NotificationEngine(db).emit(
            event_code="M1_CLAIM_SENT",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "due_at": (
                    due_at.strftime("%d.%m.%Y %H:%M UTC")
                    if due_at
                    else "уточняется"
                ),
                "next_action": case.next_action or "Ожидать 30 дней после претензии",
            },
            dedupe_key=f"case:{case.id}:claim-sent:{source_version}",
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
        "claim_due_at": due_at.isoformat() if due_at else None,
        "updated_at": case.updated_at.isoformat(),
    }


@router.post("/cases/{case_id}/court/open")
async def open_court_stage(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    comment, expected_status, expected_updated_at = _snapshot(payload)
    if not comment or len(comment) < 5:
        raise HTTPException(status_code=400, detail="Укажите основание открытия судебного этапа")
    try:
        case = await assigned_case(
            db,
            case_id,
            actor.lawyer.id,
            for_update=True,
        )
        assert_case_snapshot(
            case,
            expected_status=expected_status,
            expected_updated_at=expected_updated_at,
        )
        source_version = case.updated_at.isoformat()
        await M1ClaimService(db).open_court_stage(
            case=case,
            lawyer_id=actor.lawyer.id,
            comment=comment,
        )
        await CaseSLAService(db).record_lawyer_activity(
            case=case,
            lawyer_id=actor.lawyer.id,
            action="COURT_STAGE_STARTED",
            comment=comment,
        )
        await NotificationEngine(db).emit(
            event_code="COURT_STAGE_STARTED",
            case_id=case.id,
            payload={"case_number": case.case_number},
            dedupe_key=f"case:{case.id}:court-open:{source_version}",
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
