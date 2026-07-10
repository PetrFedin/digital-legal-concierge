from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.case import Case
from app.models.document import Document
from app.models.payment import Payment
from app.models.consultation import Consultation
from app.models.notification import Notification
from app.models.analytics_event import AnalyticsEvent
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.lawyer import Lawyer

router = APIRouter(prefix="/runtime", tags=["runtime"])


def check(token: str | None):
    if token != settings.admin_api_token:
        raise HTTPException(status_code=401, detail="bad token")


@router.get("/snapshot")
async def snapshot(db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None)):
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
async def case_full(case_id: int, db: AsyncSession = Depends(get_db), x_admin_token: str | None = Header(default=None)):
    check(x_admin_token)
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalars().first()
    if not case:
        raise HTTPException(404, "case not found")
    documents = (await db.execute(select(Document).where(Document.case_id == case_id))).scalars().all()
    payments = (await db.execute(select(Payment).where(Payment.case_id == case_id))).scalars().all()
    consultations = (await db.execute(select(Consultation).where(Consultation.case_id == case_id))).scalars().all()
    audit = (await db.execute(select(AuditLog).where(AuditLog.entity_type == "case").where(AuditLog.entity_id == case_id).order_by(AuditLog.created_at.asc()))).scalars().all()
    return {
        "case": {
            "id": case.id,
            "number": case.case_number,
            "route": case.route,
            "status": case.status,
            "next_action": case.next_action,
            "lawyer_id": case.assigned_lawyer_id,
        },
        "documents": [{"id": d.id, "type": d.document_type, "title": d.title, "status": d.status, "version": d.version} for d in documents],
        "payments": [{"id": p.id, "code": p.payment_code, "amount": str(p.amount), "status": p.status, "url": p.payment_url} for p in payments],
        "consultations": [{"id": c.id, "status": c.status, "scheduled_at": c.scheduled_at, "decision": c.decision} for c in consultations],
        "audit": [{"action": a.action, "old": a.old_value, "new": a.new_value, "comment": a.comment} for a in audit],
    }
