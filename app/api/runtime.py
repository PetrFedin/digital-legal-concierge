from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.analytics_event import AnalyticsEvent
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User
from app.release import release_metadata

router = APIRouter(prefix="/runtime", tags=["runtime"])


def check(token: str | None):
    if token != settings.admin_api_token:
        raise HTTPException(status_code=401, detail="bad token")


@router.get("/release")
async def release_info():
    """Non-sensitive build identity for deploy verification and rollback."""

    return {"ok": True, **release_metadata()}


@router.get("/snapshot")
async def snapshot(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)

    async def count(model):
        result = await db.execute(select(func.count(model.id)))
        return int(result.scalar_one())

    return {
        "users": await count(User),
        "lawyers": await count(Lawyer),
        "cases": await count(Case),
        "documents": await count(Document),
        "payments": await count(Payment),
        "consultations": await count(Consultation),
        "notifications": await count(Notification),
        "analytics_events": await count(AnalyticsEvent),
        "audit_events": await count(AuditLog),
    }


@router.get("/case/{case_id}/full")
async def case_full(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    check(x_admin_token)
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalars().first()
    if not case:
        raise HTTPException(404, "case not found")
    documents = (
        await db.execute(select(Document).where(Document.case_id == case_id))
    ).scalars().all()
    payments = (
        await db.execute(select(Payment).where(Payment.case_id == case_id))
    ).scalars().all()
    consultations = (
        await db.execute(select(Consultation).where(Consultation.case_id == case_id))
    ).scalars().all()
    audit = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.entity_type == "case")
            .where(AuditLog.entity_id == case_id)
            .order_by(AuditLog.created_at.asc())
        )
    ).scalars().all()
    return {
        "case": {
            "id": case.id,
            "number": case.case_number,
            "route": case.route,
            "status": case.status,
            "next_action": case.next_action,
            "lawyer_id": case.assigned_lawyer_id,
        },
        "documents": [
            {
                "id": document.id,
                "type": document.document_type,
                "title": document.title,
                "status": document.status,
                "version": document.version,
            }
            for document in documents
        ],
        "payments": [
            {
                "id": payment.id,
                "code": payment.payment_code,
                "amount": str(payment.amount),
                "status": payment.status,
                "url": payment.payment_url,
            }
            for payment in payments
        ],
        "consultations": [
            {
                "id": consultation.id,
                "status": consultation.status,
                "scheduled_at": consultation.scheduled_at,
                "decision": consultation.decision,
            }
            for consultation in consultations
        ],
        "audit": [
            {
                "action": event.action,
                "old": event.old_value,
                "new": event.new_value,
                "comment": event.comment,
            }
            for event in audit
        ],
    }
