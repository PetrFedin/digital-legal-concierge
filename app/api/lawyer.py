from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.lawyer.lawyer_decisions import LawyerDecisionService
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.lawyer import Lawyer
from app.models.message import Message
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_SUPERADMIN,
    decode_access_token,
    normalize_roles,
)

router = APIRouter(prefix="/lawyer", tags=["lawyer"])


def extract_token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def require_lawyer_context(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
) -> tuple[dict, AdminUser | None, Lawyer | None]:
    payload = decode_access_token(extract_token(request, header_token))
    if not payload:
        raise HTTPException(status_code=401, detail="Требуется вход")

    roles = set(normalize_roles(payload.get("roles")))
    if not roles.intersection({ROLE_LAWYER, ROLE_ADMIN, ROLE_SUPERADMIN}):
        raise HTTPException(status_code=403, detail="Нет доступа к кабинету юриста")

    user = None
    lawyer = None
    user_id = int(payload.get("uid") or 0)
    if user_id:
        user = (
            await db.execute(select(AdminUser).where(AdminUser.id == user_id, AdminUser.is_active.is_(True)))
        ).scalars().first()
        if not user:
            raise HTTPException(status_code=401, detail="Учетная запись отключена")
        lawyer = (
            await db.execute(select(Lawyer).where(Lawyer.admin_user_id == user.id))
        ).scalars().first()
        if not lawyer and user.email:
            lawyer = (
                await db.execute(select(Lawyer).where(Lawyer.email == user.email))
            ).scalars().first()
            if lawyer and lawyer.admin_user_id is None:
                lawyer.admin_user_id = user.id
                await db.flush()

    if ROLE_LAWYER in roles and not lawyer:
        raise HTTPException(
            status_code=409,
            detail="Для учетной записи не создан профиль юриста. Обратитесь к суперадминистратору.",
        )
    if lawyer and not lawyer.is_active:
        raise HTTPException(status_code=403, detail="Профиль юриста отключен")
    return payload, user, lawyer


def can_manage_all(payload: dict) -> bool:
    roles = set(normalize_roles(payload.get("roles")))
    return bool(roles.intersection({ROLE_ADMIN, ROLE_SUPERADMIN}))


async def get_case_or_404(db: AsyncSession, case_id: int) -> Case:
    case = (await db.execute(select(Case).where(Case.id == case_id))).scalars().first()
    if not case:
        raise HTTPException(status_code=404, detail="Дело не найдено")
    return case


def assert_case_access(case: Case, payload: dict, lawyer: Lawyer | None) -> None:
    if can_manage_all(payload):
        return
    if not lawyer or case.assigned_lawyer_id != lawyer.id:
        raise HTTPException(status_code=403, detail="Дело не назначено этому юристу")


@router.get("/workspace")
async def workspace(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    payload, user, lawyer = await require_lawyer_context(request, db, x_admin_token)
    all_access = can_manage_all(payload)

    case_query = select(Case).where(Case.is_archived.is_(False))
    if not all_access:
        case_query = case_query.where(Case.assigned_lawyer_id == lawyer.id)
    cases = (await db.execute(case_query.order_by(Case.created_at.desc()).limit(100))).scalars().all()

    consultation_query = select(Consultation)
    if not all_access:
        consultation_query = consultation_query.where(Consultation.lawyer_id == lawyer.id)
    consultations = (
        await db.execute(consultation_query.order_by(Consultation.scheduled_at.asc()).limit(100))
    ).scalars().all()

    unassigned_count = (
        await db.execute(
            select(func.count(Case.id)).where(
                Case.assigned_lawyer_id.is_(None),
                Case.is_archived.is_(False),
            )
        )
    ).scalar_one()

    case_ids = [case.id for case in cases]
    documents_pending = 0
    unread_messages = 0
    if case_ids:
        documents_pending = (
            await db.execute(
                select(func.count(Document.id)).where(
                    Document.case_id.in_(case_ids),
                    Document.status.in_(["UPLOADED", "PENDING", "ON_REVIEW"]),
                )
            )
        ).scalar_one()
        unread_messages = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.case_id.in_(case_ids),
                    or_(Message.is_read.is_(False), Message.is_read.is_(None)),
                )
            )
        ).scalar_one()

    await db.commit()
    return {
        "account": {
            "id": user.id if user else None,
            "username": payload.get("username"),
            "roles": normalize_roles(payload.get("roles")),
            "lawyer_id": lawyer.id if lawyer else None,
            "lawyer_name": lawyer.full_name if lawyer else None,
            "all_access": all_access,
        },
        "summary": {
            "cases": len(cases),
            "unassigned_cases": unassigned_count if all_access else None,
            "consultations": len(consultations),
            "documents_pending": documents_pending,
            "unread_messages": unread_messages,
        },
        "cases": [
            {
                "id": case.id,
                "case_number": case.case_number,
                "title": case.title,
                "route": case.route,
                "status": case.status,
                "next_action": case.next_action,
                "assigned_lawyer_id": case.assigned_lawyer_id,
                "created_at": case.created_at.isoformat() if case.created_at else None,
            }
            for case in cases
        ],
        "consultations": [
            {
                "id": consultation.id,
                "case_id": consultation.case_id,
                "status": consultation.status,
                "scheduled_at": consultation.scheduled_at.isoformat() if consultation.scheduled_at else None,
                "subject_type": consultation.subject_type,
                "client_description": consultation.client_description,
            }
            for consultation in consultations
        ],
    }


@router.post("/cases/{case_id}/accept")
async def accept(
    case_id: int,
    request: Request,
    lawyer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    payload, _, lawyer = await require_lawyer_context(request, db, x_admin_token)
    case = await get_case_or_404(db, case_id)
    target_lawyer_id = lawyer_id if can_manage_all(payload) and lawyer_id else (lawyer.id if lawyer else None)
    if not target_lawyer_id:
        raise HTTPException(status_code=400, detail="Не указан юрист")
    if case.assigned_lawyer_id and case.assigned_lawyer_id != target_lawyer_id and not can_manage_all(payload):
        raise HTTPException(status_code=409, detail="Дело уже назначено другому юристу")
    await LawyerDecisionService(db).accept_m1_case(case=case, lawyer_id=target_lawyer_id)
    await db.commit()
    return {"ok": True, "case_id": case.id, "lawyer_id": target_lawyer_id}


@router.post("/cases/{case_id}/request-documents")
async def request_docs(
    case_id: int,
    payload_data: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    payload, _, lawyer = await require_lawyer_context(request, db, x_admin_token)
    case = await get_case_or_404(db, case_id)
    assert_case_access(case, payload, lawyer)
    actor_lawyer_id = lawyer.id if lawyer else case.assigned_lawyer_id
    if not actor_lawyer_id:
        raise HTTPException(status_code=409, detail="Сначала назначьте юриста")
    comment = str(payload_data.get("comment") or "Нужны дополнительные документы").strip()
    await LawyerDecisionService(db).request_more_documents(
        case=case,
        lawyer_id=actor_lawyer_id,
        comment=comment,
    )
    await db.commit()
    return {"ok": True, "case_id": case.id, "comment": comment}
