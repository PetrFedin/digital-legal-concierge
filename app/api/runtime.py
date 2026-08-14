from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
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
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import resolve_document_actor

router = APIRouter(prefix="/runtime", tags=["runtime"])


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("/release")
async def release_info():
    """Non-sensitive build identity for deploy verification and rollback."""

    return {"ok": True, **release_metadata()}


@router.get("/snapshot")
async def snapshot(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)

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
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalars().first()
    if not case:
        raise HTTPException(404, "Дело не найдено")
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
            .limit(500)
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
                "provider": payment.provider,
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
        # Raw old_value/new_value may contain client data, provider details or
        # historical technical payloads. The timeline/audit centers remain the
        # canonical place for authorized detailed inspection.
        "audit": [
            {
                "id": event.id,
                "action": event.action,
                "actor_type": event.actor_type,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in audit
        ],
    }
