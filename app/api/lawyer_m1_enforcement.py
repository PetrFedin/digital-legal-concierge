from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.lawyer import assigned_case, assert_case_snapshot
from app.db.session import get_db
from app.domain.cases.m1_enforcement_service import M1EnforcementService
from app.domain.cases.sla_service import CaseSLAError, CaseSLAService
from app.domain.notifications.notification_engine import NotificationEngine
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(prefix="/lawyer", tags=["lawyer-m1-enforcement"])


@router.post("/cases/{case_id}/enforcement/money-received")
async def record_money_received(
    case_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await require_lawyer_actor(db, x_admin_token)
    amount = payload.get("amount")
    comment = str(payload.get("comment") or "").strip()
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
        source_version = case.updated_at.isoformat()
        result = await M1EnforcementService(db).record_money_received(
            case=case,
            lawyer_id=actor.lawyer.id,
            amount=amount,
            comment=comment,
        )
        await CaseSLAService(db).record_lawyer_activity(
            case=case,
            lawyer_id=actor.lawyer.id,
            action="M1_MONEY_RECEIVED_RECORDED",
            comment=comment or f"Получено {result.recovered_amount} ₽",
        )
        await NotificationEngine(db).emit(
            event_code="M1_MONEY_RECEIVED",
            case_id=case.id,
            payload={
                "case_number": case.case_number,
                "amount": str(result.recovered_amount),
                "success_fee": str(result.success_fee_amount),
            },
            dedupe_key=f"case:{case.id}:money-received:{source_version}",
        )
        await db.commit()
        await db.refresh(case)
        await db.refresh(result.payment)
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
        "recovered_amount": str(result.recovered_amount),
        "success_fee_amount": str(result.success_fee_amount),
        "payment_id": result.payment.id,
        "payment_status": result.payment.status,
        "next_action": case.next_action,
        "updated_at": case.updated_at.isoformat(),
    }
