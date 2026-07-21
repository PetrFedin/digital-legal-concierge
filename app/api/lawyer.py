from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.consultations.payment_lifecycle_service import (
    ConsultationPaymentLifecycleError,
    ConsultationPaymentLifecycleService,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.lawyer.lawyer_decisions import LawyerDecisionService
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.lawyer import Lawyer
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_SUPERADMIN,
    decode_access_token,
    normalize_roles,
)

router = APIRouter(prefix="/lawyer", tags=["lawyer"])


def require_staff(token: str | None) -> dict:
    payload = decode_access_token(token)
    roles = set(normalize_roles(payload.get("roles") if payload else None))
    if not payload or not roles.intersection(
        {ROLE_SUPERADMIN, ROLE_ADMIN, ROLE_LAWYER}
    ):
        raise HTTPException(403, "Доступ только для юриста или администратора")
    return payload


async def resolve_authenticated_lawyer(
    *,
    token: str | None,
    db: AsyncSession,
) -> Lawyer:
    payload = decode_access_token(token)
    roles = set(normalize_roles(payload.get("roles") if payload else None))
    if not payload or ROLE_LAWYER not in roles:
        raise HTTPException(403, "Подтверждение доступно только юристу")

    try:
        admin_user_id = int(payload.get("uid"))
    except (TypeError, ValueError):
        raise HTTPException(403, "Не удалось определить аккаунт юриста")
    if admin_user_id <= 0:
        raise HTTPException(
            403,
            "Для подтверждения войдите под персональным аккаунтом юриста",
        )

    admin_user = (
        await db.execute(
            select(AdminUser).where(
                AdminUser.id == admin_user_id,
                AdminUser.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if admin_user is None:
        raise HTTPException(403, "Аккаунт юриста не найден или отключён")

    lawyer = (
        await db.execute(
            select(Lawyer).where(
                Lawyer.is_active.is_(True),
                Lawyer.email.is_not(None),
                func.lower(Lawyer.email) == admin_user.email.strip().lower(),
            )
        )
    ).scalar_one_or_none()
    if lawyer is None:
        raise HTTPException(
            403,
            "Для аккаунта не настроен активный профиль юриста с тем же email",
        )
    return lawyer


async def get_case_or_404(*, case_id: int, db: AsyncSession) -> Case:
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(404, "Дело не найдено")
    return case


@router.get("/consultations/pending-confirmation")
async def pending_consultations(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    lawyer = await resolve_authenticated_lawyer(
        token=x_admin_token,
        db=db,
    )
    rows = (
        await db.execute(
            select(Consultation, Case)
            .join(Case, Case.id == Consultation.case_id)
            .where(
                Consultation.lawyer_id == lawyer.id,
                Consultation.status
                == ConsultationStatus.PAID_PENDING_CONFIRMATION.value,
            )
            .order_by(
                Consultation.scheduled_at.asc(),
                Consultation.id.asc(),
            )
        )
    ).all()
    return [
        {
            "consultation_id": consultation.id,
            "case_id": case.id,
            "case_number": case.case_number,
            "case_title": case.title,
            "status": consultation.status,
            "consultation_type": consultation.consultation_type,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
            "slot_id": consultation.slot_id,
            "client_description": consultation.client_description,
        }
        for consultation, case in rows
    ]


@router.post("/cases/{case_id}/confirm-consultation")
async def confirm_consultation(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    lawyer = await resolve_authenticated_lawyer(
        token=x_admin_token,
        db=db,
    )
    case = await get_case_or_404(case_id=case_id, db=db)
    try:
        consultation = await ConsultationPaymentLifecycleService(
            db
        ).confirm_by_lawyer(
            case=case,
            lawyer_id=lawyer.id,
            source="lawyer_api",
        )
        await db.commit()
    except ConsultationPaymentLifecycleError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc

    return {
        "ok": True,
        "case_id": case.id,
        "case_status": case.status,
        "consultation_id": consultation.id,
        "consultation_status": consultation.status,
        "lawyer_id": lawyer.id,
        "slot_id": consultation.slot_id,
        "scheduled_at": (
            consultation.scheduled_at.isoformat()
            if consultation.scheduled_at
            else None
        ),
    }


@router.post("/cases/{case_id}/accept")
async def accept(
    case_id: int,
    lawyer_id: int = 1,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    case = await get_case_or_404(case_id=case_id, db=db)
    await LawyerDecisionService(db).accept_m1_case(
        case=case,
        lawyer_id=lawyer_id,
    )
    await db.commit()
    return {"ok": True}


@router.post("/cases/{case_id}/request-documents")
async def request_docs(
    case_id: int,
    payload: dict,
    lawyer_id: int = 1,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    case = await get_case_or_404(case_id=case_id, db=db)
    comment = str(payload.get("comment") or "").strip()
    if not comment:
        comment = "Нужны дополнительные документы"
    await LawyerDecisionService(db).request_more_documents(
        case=case,
        lawyer_id=lawyer_id,
        comment=comment,
    )
    await db.commit()
    return {"ok": True}
